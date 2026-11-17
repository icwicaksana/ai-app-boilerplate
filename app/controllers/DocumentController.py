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

    def _storage_path(self, workspace: str, document_id: str) -> Path:
        base = Path(self.settings.RAG_STORAGE_DIR).resolve()
        target = (base / workspace / f"{document_id}.bin").resolve()
        if base not in target.parents:
            from app.core.errors import BadRequestError

            # 400 jujur (bukan ValueError → 500); praktis tak tercapai
            # (workspace JWT + doc_id UUID terkendali).
            raise BadRequestError(f"path blob di luar storage: {workspace}")
        return target

    async def enqueue(self, *, workspace: str, user: str, filename: str,
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
        target = self._storage_path(workspace, document_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        try:
            doc = await repo.create_document(
                self.pool, workspace=workspace, source_type=source_type,
                source_uri=str(Path(workspace) / f"{document_id}.bin"),
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
            # v1.0 R10: reuse-or-create atomik (anti job ganda konkuren).
            job, _ = await repo.reuse_or_create_job(
                self.pool, workspace=workspace, document_id=str(doc["id"]))
            return {"document_id": str(doc["id"]), "job_id": job["id"],
                    "deduped": True}
        job = await repo.create_job(self.pool, workspace=workspace,
                                    document_id=document_id)
        return {"document_id": document_id, "job_id": job["id"],
                "deduped": False}

    async def retry(self, *, workspace: str, job_id: int,
                    ocr: str | None) -> dict | None:
        """Reset job failed/dead (+override ocr). None = tak boleh/tak ada."""
        job = await repo.retry_job(self.pool, workspace=workspace, job_id=job_id)
        if job is None:
            return None
        if ocr is not None:
            await repo.set_document_status(
                self.pool, workspace=workspace,
                document_id=str(job["document_id"]), status="pending",
                meta_patch={"ocr": ocr, "ocr_required": False})
        return job

    async def delete(self, *, workspace: str, document_id: str) -> dict:
        """Hapus dokumen di semua backend + tombstone + cancel job.

        v1.0 R9: best-effort per backend (satu gagal tak menggagalkan yang
        lain) + tombstone/cancel SELALU jalan + ringkasan jujur.
        Return {"ok": False} bila dokumen tak ada; {"ok": True, ...} bila ada.
        """
        doc = await repo.get_document(self.pool, workspace=workspace,
                                      document_id=document_id)
        if doc is None:
            return {"ok": False}
        backends: dict[str, int] = {"pg": 0, "qdrant": 0, "graph_entities": 0,
                                    "graph_edges": 0}
        errors: list[str] = []

        async def _try(name: str, fn):
            try:
                return await fn()
            except Exception as exc:  # best-effort: catat, lanjutkan
                log.warning("delete %s backend %s gagal: %s", document_id,
                            name, exc)
                errors.append(f"{name}: {type(exc).__name__}")
                return None

        from app.infra.rag.pgvector import PgVectorStore

        async def _pg():
            pg = PgVectorStore(self.pool, workspace=workspace)
            return await pg.delete_document(doc_id=document_id)

        backends["pg"] = await _try("pg", _pg) or 0
        # SELALU coba Qdrant bila klien ada (P1-7) — backend bisa pindah di
        # tengah umur dokumen (orphan tak terlihat bila hanya ikut backend).
        if self.rag is not None and self.rag.qdrant is not None:
            from app.infra.rag.qdrant import QdrantStore, collection_name

            async def _qdrant():
                q = QdrantStore(
                    self.rag.qdrant,
                    collection_name(self.rag.dims,
                                    self.settings.RAG_EMBED_MODEL),
                    workspace=workspace)
                return await q.delete_document(doc_id=document_id)

            backends["qdrant"] = await _try("qdrant", _qdrant) or 0
        # SELALU coba graph (D10, pola P1-7) — flag bisa mati di tengah umur.
        from app.infra.rag.pg_graph import PgGraphStore

        async def _graph():
            g = PgGraphStore(self.pool, workspace=workspace)
            return await g.delete_subgraph(doc_id=document_id)

        graph_out = await _try("graph", _graph)
        if graph_out:
            backends["graph_entities"], backends["graph_edges"] = graph_out
        # Tombstone + cancel job TAK BOLEH terlewat walau backend gagal.
        tombstoned = await _try(
            "tombstone",
            lambda: repo.tombstone_document(
                self.pool, workspace=workspace, document_id=document_id))
        n_cancelled = await _try(
            "cancel_jobs",
            lambda: repo.cancel_jobs_for_document(
                self.pool, workspace=workspace, document_id=document_id))
        try:
            self._storage_path(workspace, document_id).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("blob %s gagal dihapus: %s", document_id, exc)
            errors.append(f"blob: {type(exc).__name__}")
        log.info("dokumen %s dihapus (pg=%d qdrant=%d graph=%d/%d "
                 "tombstone=%s cancelled=%s errors=%s)",
                 document_id, backends["pg"], backends["qdrant"],
                 backends["graph_entities"], backends["graph_edges"],
                 tombstoned, n_cancelled, errors or "-")
        return {"ok": True, "backends": backends,
                "tombstoned": bool(tombstoned),
                "jobs_cancelled": n_cancelled or 0, "errors": errors}

    # -- baca --

    async def get(self, *, workspace: str, document_id: str) -> dict | None:
        """Detail + job terbaru + jumlah chunk. None = asing/tak ada (404)."""
        doc = await repo.get_document(self.pool, workspace=workspace,
                                      document_id=document_id)
        if doc is None:
            return None
        job = await repo.latest_job_for_document(
            self.pool, workspace=workspace, document_id=document_id)
        n = await repo.count_chunks(self.pool, workspace=workspace,
                                    document_id=document_id)
        return {"document": doc, "job": job, "chunks": n}

    async def list(self, *, workspace: str, status: str | None,
                   limit: int, offset: int):
        return await repo.list_documents(
            self.pool, workspace=workspace, status=status,
            limit=limit, offset=offset)

    async def job_status(self, *, workspace: str, job_id: int) -> dict | None:
        return await repo.get_job(self.pool, workspace=workspace, job_id=job_id)

    async def search(self, *, workspace: str, query: str, k: int,
                     filters: dict | None, clients) -> dict:
        """Debug retrieval: HybridRetriever penuh (workspace-bound, D2)."""
        from app.infra.rag.factory import retriever_for_workspace

        retriever = retriever_for_workspace(
            self.settings, clients, self.pool, workspace=workspace)
        res = await retriever.search(query, k=k, filters=filters)
        trace = res.get("trace") or {}
        counts = trace.get("counts", {})
        log.info("retrieval workspace=%s status=%s top=%.4f final=%d/%d "
                 "total_ms=%.1f degraded=%s",
                 workspace, res["status"], trace.get("top_score", 0.0),
                 counts.get("final", 0), counts.get("candidates", 0),
                 trace.get("timings_ms", {}).get("total", 0.0),
                 ",".join(res["degraded"]) or "-")
        return {"matches": res["chunks"], "status": res["status"],
                "degraded": res["degraded"], "trace": trace,
                "backend": self.settings.RAG_BACKEND}
