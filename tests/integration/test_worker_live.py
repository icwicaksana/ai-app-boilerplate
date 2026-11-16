"""Live worker ingest: MD penuh (parse→chunk→embed→index) + jalur dead.

Butuh app-db + Infinity. Tanpa API (repo langsung) — API diuji terpisah.
"""

import hashlib

import pytest

pytestmark = pytest.mark.integration


def _md_bytes() -> bytes:
    return ("## Kebijakan Retur\n\nBarang bisa diretur maksimal 14 hari "
            "dengan nota pembelian.\n\n| SKU | Harga |\n|---|---|\n"
            "| A1 | 5000 |\n").encode()


async def _purge_prefix(pool, prefix: str) -> None:
    """Hapus residu run sebelumnya (queue shared + tmp pytest dihapus otomatis).

    Tanpa ini claim_job mengambil job basi yang blob-nya sudah tak ada.
    """
    from conftest import purge_stale_test_jobs

    await purge_stale_test_jobs(pool)  # queue GLOBAL: bersihkan workspace test
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM documents WHERE workspace LIKE %s", (prefix + "%",))


@pytest.mark.asyncio
async def test_live_worker_md_sampai_ready(db_url, embed_url, tag, tmp_path):
    from app.core.config import Settings
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.pgvector import PgVectorStore
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    workspace = f"it-w-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    from app.infra.rag_migrate import apply_rag_migration

    await apply_rag_migration(pool)
    await _purge_prefix(pool, "it-w-")
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    try:
        blob = _md_bytes()
        rel = f"{workspace}/doc1.bin"
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        doc = await repo.create_document(
            pool, workspace=workspace, source_type="md", source_uri=rel,
            title="retur.md", lang="id", size_bytes=len(blob),
            sha256=hashlib.sha256(blob).hexdigest(), parser="auto",
            chunk_preset="markdown", created_by="test")
        job = await repo.create_job(pool, workspace=workspace,
                                    document_id=str(doc["id"]))
        claimed = await repo.claim_job(pool, worker_id="test-w")
        assert claimed is not None and claimed["id"] == job["id"]

        parsers, ocr = build_parsers(settings)
        ctx = WorkerCtx(settings=settings, pool=pool, worker_id="test-w",
                        embed=embed, tok=BgeTokenizer(cache_dir=".hf-cache"),
                        qdrant=None, cache=None, parsers=parsers, ocr=ocr)
        state = await process_job(ctx, claimed)
        assert state == "done"

        doc2 = await repo.get_document(pool, workspace=workspace,
                                       document_id=str(doc["id"]))
        assert doc2["status"] == "ready"
        assert doc2["meta"]["chunks"] >= 2  # prose + baris tabel

        store = PgVectorStore(pool, workspace=workspace)
        qv = (await embed.embed_texts(["berapa lama retur barang?"]))[0]
        hits = await store.search_dense(qv, k=5)
        assert hits and "14 hari" in hits[0]["content"]
        assert hits[0]["chunk_id"]  # stabil sha256 (top-level, bukan metadata)
        assert hits[0]["metadata"]["source_type"] == "md"
    finally:
        await embed.aclose()
        await close_pool(pool)
        # cleanup best-effort
        try:
            pool2 = create_pool(settings)
            await open_pool(pool2)
            async with pool2.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
            await close_pool(pool2)
        except Exception:  # noqa: BLE001 - cleanup best-effort
            pass


@pytest.mark.asyncio
async def test_live_worker_kosong_jadi_dead(db_url, embed_url, tag, tmp_path):
    from app.core.config import Settings
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    workspace = f"it-we-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    from app.infra.rag_migrate import apply_rag_migration

    await apply_rag_migration(pool)
    await _purge_prefix(pool, "it-we-")
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    try:
        blob = b"\n\n   \n"
        rel = f"{workspace}/empty.bin"
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        doc = await repo.create_document(
            pool, workspace=workspace, source_type="md", source_uri=rel,
            title="empty.md", lang="id", size_bytes=len(blob),
            sha256=hashlib.sha256(blob).hexdigest(), parser="auto",
            chunk_preset="markdown", created_by="test")
        await repo.create_job(pool, workspace=workspace,
                              document_id=str(doc["id"]))
        claimed = await repo.claim_job(pool, worker_id="test-w")
        assert claimed is not None
        parsers, ocr = build_parsers(settings)
        ctx = WorkerCtx(settings=settings, pool=pool, worker_id="test-w",
                        embed=embed, tok=BgeTokenizer(cache_dir=".hf-cache"),
                        qdrant=None, cache=None, parsers=parsers, ocr=ocr)
        state = await process_job(ctx, claimed)
        assert state == "dead"
        job2 = await repo.get_job(pool, workspace=workspace, job_id=claimed["id"])
        assert job2["state"] == "dead" and "EMPTY_DOCUMENT" in job2["last_error"]
    finally:
        await embed.aclose()
        await close_pool(pool)


@pytest.mark.asyncio
async def test_live_worker_pdf_pindai_minta_ocr(db_url, embed_url, tag,
                                                tmp_path):
    from app.core.config import Settings
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    import pymupdf

    workspace = f"it-wo-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    from app.infra.rag_migrate import apply_rag_migration

    await apply_rag_migration(pool)
    await _purge_prefix(pool, "it-wo-")
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    try:
        doc_pdf = pymupdf.open()
        pg = doc_pdf.new_page()
        pix = pymupdf.Pixmap(pymupdf.Colorspace(pymupdf.CS_RGB),
                             pymupdf.Rect(0, 0, 200, 60), False)
        pg.insert_image(pymupdf.Rect(50, 50, 250, 110), pixmap=pix)
        blob = doc_pdf.tobytes()
        rel = f"{workspace}/scan.bin"
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        doc = await repo.create_document(
            pool, workspace=workspace, source_type="pdf", source_uri=rel,
            title="scan.pdf", lang="id", size_bytes=len(blob),
            sha256=hashlib.sha256(blob).hexdigest(), parser="auto",
            chunk_preset="pdf", created_by="test",
            meta={"ocr": "auto"})
        await repo.create_job(pool, workspace=workspace,
                              document_id=str(doc["id"]))
        claimed = await repo.claim_job(pool, worker_id="test-w")
        assert claimed is not None
        parsers, ocr = build_parsers(settings)
        ctx = WorkerCtx(settings=settings, pool=pool, worker_id="test-w",
                        embed=embed, tok=BgeTokenizer(cache_dir=".hf-cache"),
                        qdrant=None, cache=None, parsers=parsers, ocr=ocr)
        state = await process_job(ctx, claimed)
        assert state == "dead"
        doc2 = await repo.get_document(pool, workspace=workspace,
                                       document_id=str(doc["id"]))
        assert doc2["meta"].get("ocr_required") is True
        job2 = await repo.get_job(pool, workspace=workspace, job_id=claimed["id"])
        assert "OCR_REQUIRED" in job2["last_error"]
    finally:
        await embed.aclose()
        await close_pool(pool)
