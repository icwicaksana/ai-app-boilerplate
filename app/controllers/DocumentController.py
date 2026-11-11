"""Controller dokumen/ingest (D12): upload → antre → status → hapus → cari.

Tanggung jawab route-level: validasi transport (ukuran, MIME, traversal),
penyimpanan blob, panggil repo. TIDAK memproses isi (itu worker).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from app.core.config import Settings
from app.core.errors import (
    DocumentNotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaError,
)
from app.core.logging import get_logger
from app.infra import ingest_repo as repo
from app.rag.parsers.sniff import DOCX, MD, PDF, PPTX, TXT, XLSX, sniff_mime

log = get_logger(__name__)

SOURCE_BY_MIME = {PDF: "pdf", DOCX: "docx", PPTX: "pptx", XLSX: "xlsx",
                  MD: "md", TXT: "txt"}
_MACRO_EXTS = (".xlsm", ".xlsb", ".xltm")


class DocumentController:
    def __init__(self, settings: Settings, pool, rag_clients=None) -> None:
        self.settings = settings
        self.pool = pool
        self.rag = rag_clients

    # -- tulis --

    def _storage_path(self, tenant: str, document_id: str) -> Path:
        base = Path(self.settings.RAG_STORAGE_DIR).resolve()
        target = (base / tenant / f"{document_id}.bin").resolve()
        if base not in target.parents:
            from app.core.errors import BadRequestError

            # 400 jujur (bukan ValueError → 500); praktis tak tercapai
            # (tenant JWT + doc_id UUID terkendali).
            raise BadRequestError(f"path blob di luar storage: {tenant}")
        return target

    async def enqueue(self, *, tenant: str, user: str, filename: str,
                      blob: bytes, preset: str, ocr: str,
                      lang: str = "id") -> dict:
        """Validasi → simpan blob → doc + job. Return {document_id, job_id}."""
        if len(blob) > self.settings.RAG_MAX_UPLOAD_BYTES:
            raise PayloadTooLargeError(len(blob),
                                       self.settings.RAG_MAX_UPLOAD_BYTES)
        if not blob:
            raise UnsupportedMediaError("berkas kosong")
        lowered = (filename or "").lower()
        if lowered.endswith(_MACRO_EXTS):
            raise UnsupportedMediaError(
                f"ekstensi {lowered.rsplit('.', 1)[-1]} (macro) ditolak (D10).")
        mime = sniff_mime(blob, filename)
        source_type = SOURCE_BY_MIME.get(mime)
        if source_type is None:
            raise UnsupportedMediaError(f"sniff:{mime}")
        if source_type == "xlsx" and preset != "factoid":
            log.info("xlsx preset %s → baris tetap difactoid-kan", preset)

        document_id = str(uuid.uuid4())
        sha = hashlib.sha256(blob).hexdigest()
        target = self._storage_path(tenant, document_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        try:
            doc = await repo.create_document(
                self.pool, tenant=tenant, source_type=source_type,
                source_uri=str(Path(tenant) / f"{document_id}.bin"),
                title=filename or document_id, lang=lang,
                size_bytes=len(blob), sha256=sha, parser="auto",
                chunk_preset=preset, created_by=user,
                meta={"mime": mime, "ocr": ocr}, document_id=document_id)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if doc.get("_existing"):
            # Dedup: blob baru tak terpakai (doc lama menang).
            target.unlink(missing_ok=True)
            job = await repo.latest_job_for_document(
                self.pool, tenant=tenant, document_id=str(doc["id"]))
            # Reuse job hidup (anti duplikat saat idempotency Valkey hilang,
            # P1-5); job mati (dead) + upload ulang = niat proses ulang.
            if job is None or job["state"] == "dead":
                job = await repo.create_job(
                    self.pool, tenant=tenant, document_id=str(doc["id"]))
            return {"document_id": str(doc["id"]), "job_id": job["id"],
                    "deduped": True}
        job = await repo.create_job(self.pool, tenant=tenant,
                                    document_id=document_id)
        return {"document_id": document_id, "job_id": job["id"],
                "deduped": False}

    async def retry(self, *, tenant: str, job_id: int,
                    ocr: str | None) -> dict | None:
        """Reset job failed/dead (+override ocr). None = tak boleh/tak ada."""
        job = await repo.retry_job(self.pool, tenant=tenant, job_id=job_id)
        if job is None:
            return None
        if ocr is not None:
            await repo.set_document_status(
                self.pool, tenant=tenant,
                document_id=str(job["document_id"]), status="pending",
                meta_patch={"ocr": ocr, "ocr_required": False})
        return job

    async def delete(self, *, tenant: str, document_id: str) -> bool:
        """Tombstone + hapus chunks PG + vektor Qdrant + blob. False = tak ada."""
        doc = await repo.get_document(self.pool, tenant=tenant,
                                      document_id=document_id)
        if doc is None:
            return False
        from app.infra.rag.pgvector import PgVectorStore

        pg = PgVectorStore(self.pool, tenant=tenant)
        n_pg = await pg.delete_document(doc_id=document_id)
        n_q = 0
        # SELALU coba Qdrant bila klien ada (P1-7) — backend bisa pindah di
        # tengah umur dokumen (orphan tak terlihat bila hanya ikut backend).
        if self.rag is not None and self.rag.qdrant is not None:
            from app.infra.rag.qdrant import QdrantStore, collection_name

            q = QdrantStore(
                self.rag.qdrant,
                collection_name(self.rag.dims, self.settings.RAG_EMBED_MODEL),
                tenant=tenant)
            n_q = await q.delete_document(doc_id=document_id)
        await repo.tombstone_document(self.pool, tenant=tenant,
                                      document_id=document_id)
        await repo.cancel_jobs_for_document(
            self.pool, tenant=tenant, document_id=document_id)
        try:
            self._storage_path(tenant, document_id).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("blob %s gagal dihapus: %s", document_id, exc)
        log.info("dokumen %s dihapus (pg=%d qdrant=%d)", document_id, n_pg, n_q)
        return True

    # -- baca --

    async def get(self, *, tenant: str, document_id: str) -> dict | None:
        """Detail + job terbaru + jumlah chunk. None = asing/tak ada (404)."""
        doc = await repo.get_document(self.pool, tenant=tenant,
                                      document_id=document_id)
        if doc is None:
            return None
        job = await repo.latest_job_for_document(
            self.pool, tenant=tenant, document_id=document_id)
        n = await repo.count_chunks(self.pool, tenant=tenant,
                                    document_id=document_id)
        return {"document": doc, "job": job, "chunks": n}

    async def list(self, *, tenant: str, status: str | None,
                   limit: int, offset: int):
        return await repo.list_documents(
            self.pool, tenant=tenant, status=status,
            limit=limit, offset=offset)

    async def job_status(self, *, tenant: str, job_id: int) -> dict | None:
        return await repo.get_job(self.pool, tenant=tenant, job_id=job_id)

    async def search(self, *, tenant: str, query: str, k: int,
                     filters: dict | None, clients) -> dict:
        """Debug retrieval: HybridRetriever penuh (tenant-bound, D2)."""
        from app.infra.rag.factory import retriever_for_tenant

        retriever = retriever_for_tenant(
            self.settings, clients, self.pool, tenant=tenant)
        res = await retriever.search(query, k=k, filters=filters)
        return {"matches": res["chunks"], "status": res["status"],
                "degraded": res["degraded"],
                "backend": self.settings.RAG_BACKEND}
