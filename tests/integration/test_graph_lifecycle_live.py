"""Live lifecycle graph F2: ingest→graph→search→hapus (stub extractor)."""

from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.integration


async def _run_job(db_url, embed_url, tag, *, enabled: bool,
                   title="pemasok.md", blob: bytes | None = None,
                   workspace_suffix="g08l"):
    import shutil
    from pathlib import Path

    from app.core.config import Settings
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.extract.stub import StubExtractor
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.pg_graph import PgGraphStore
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.infra.rag_migrate import apply_rag_migration
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    workspace = f"it-{workspace_suffix}-{tag}"
    blob_root = Path(".test-blobs").resolve()
    settings = Settings(
        _env_file=None, DATABASE_URL=db_url, DB_POOL_MIN=1, DB_POOL_MAX=5,
        RAG_STORAGE_DIR=str(blob_root), GRAPH_ENABLED=enabled,
        GRAPH_EXTRACTOR_MODEL="stub-deterministik")
    pool = create_pool(settings)
    await open_pool(pool)
    await apply_rag_migration(pool)
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    try:
        if blob is None:
            blob = ("PT Sumber Rasa memasok Kopi Arabika.\n\n"
                    "PT Sumber Rasa berlokasi di Bandung.\n").encode()
        rel = f"{workspace}/doc.bin"
        target = blob_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        doc = await repo.create_document(
            pool, workspace=workspace, source_type="md", source_uri=rel,
            title=title, lang="id", size_bytes=len(blob),
            sha256=hashlib.sha256(blob + workspace.encode()).hexdigest(),
            parser="auto", chunk_preset="markdown", created_by="g08l")
        job = await repo.create_job(pool, workspace=workspace,
                                    document_id=str(doc["id"]))
        claimed = await repo.claim_job(pool, worker_id="g08l")
        assert claimed is not None and claimed["id"] == job["id"]
        parsers, ocr = build_parsers(settings)
        ctx = WorkerCtx(
            settings=settings, pool=pool, worker_id="g08l", embed=embed,
            tok=BgeTokenizer(cache_dir=".hf-cache"), qdrant=None,
            cache=None, parsers=parsers, ocr=ocr,
            extractor=StubExtractor() if enabled else None)
        state = await process_job(ctx, claimed)
        assert state == "done"
        doc2 = await repo.get_document(pool, workspace=workspace,
                                       document_id=str(doc["id"]))
        store = PgGraphStore(pool, workspace=workspace)
        info = await store.graph_info()
        return {"pool": pool, "settings": settings, "workspace": workspace,
                "doc": doc2, "info": info, "store": store, "embed": embed,
                "blob_root": blob_root}
    except Exception:
        await embed.aclose()
        await close_pool(pool)
        raise


@pytest.mark.asyncio
async def test_live_graph_lifecycle_stub(db_url, embed_url, tag):
    import shutil

    from app.controllers.DocumentController import DocumentController
    from app.infra.postgres import close_pool

    ctx = await _run_job(db_url, embed_url, tag, enabled=True)
    pool, settings, workspace = ctx["pool"], ctx["settings"], ctx["workspace"]
    try:
        assert ctx["doc"]["status"] == "ready"
        meta = ctx["doc"]["meta"]
        assert meta["graph_status"] == "ok"
        assert meta["graph_edges"] >= 2
        assert meta["graph_extractor"] == "stub-deterministik@f2"
        assert ctx["info"]["edge_count"] >= 2
        hits = await ctx["store"].expand(["kopi arabika"], k=5)
        assert hits and "memasok" in hits[0]["content"]
        # Hapus operator membersihkan graph (D10).
        ctrl = DocumentController(settings=settings, pool=pool,
                                  rag_clients=None)
        assert await ctrl.delete(workspace=workspace,
                                 document_id=str(ctx["doc"]["id"])) is True
        info2 = await ctx["store"].graph_info()
        assert (info2["entity_count"], info2["edge_count"]) == (0, 0)
    finally:
        await ctx["embed"].aclose()
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
        finally:
            await close_pool(pool)
        shutil.rmtree(ctx["blob_root"] / workspace, ignore_errors=True)


@pytest.mark.asyncio
async def test_live_graph_disabled_tanpa_jejak(db_url, embed_url, tag):
    import shutil

    from app.infra.postgres import close_pool

    ctx = await _run_job(db_url, embed_url, tag, enabled=False)
    try:
        assert ctx["doc"]["meta"]["graph_status"] == "disabled"
        assert ctx["info"]["edge_count"] == 0
    finally:
        await ctx["embed"].aclose()
        workspace = ctx["workspace"]
        pool = ctx["pool"]
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
        finally:
            await close_pool(pool)
        shutil.rmtree(ctx["blob_root"] / workspace, ignore_errors=True)


@pytest.mark.asyncio
async def test_live_graph_empty_sukses_parsial(db_url, embed_url, tag):
    """GRAPH_EMPTY: dokumen valid tanpa entity → done + status empty."""
    import shutil

    from app.infra.postgres import close_pool

    ctx = await _run_job(db_url, embed_url, tag, enabled=True,
                         title="kosong.md",
                         blob=("cuaca hari ini sangat cerah sekali dan "
                               "menyenangkan untuk semua orang.\n").encode())
    try:
        assert ctx["doc"]["status"] == "ready"  # BUKAN failed
        assert ctx["doc"]["meta"]["graph_status"] == "empty"
        assert ctx["info"]["edge_count"] == 0
    finally:
        await ctx["embed"].aclose()
        workspace = ctx["workspace"]
        pool = ctx["pool"]
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
        finally:
            await close_pool(pool)
        shutil.rmtree(ctx["blob_root"] / workspace, ignore_errors=True)


@pytest.mark.asyncio
async def test_live_graph_mati_bersihkan_graph_lama(db_url, embed_url, tag):
    """Flag ON→OFF + re-ingest: graph lama dihapus (anti-stale)."""
    import shutil

    from app.infra.postgres import close_pool

    ctx = await _run_job(db_url, embed_url, tag, enabled=True,
                         workspace_suffix="g08s")
    try:
        assert ctx["info"]["edge_count"] >= 2
        pool, workspace = ctx["pool"], ctx["workspace"]
        # Re-ingest dokumen SAMA dengan flag mati.
        from app.core.config import Settings
        from app.infra import ingest_repo as repo
        from app.infra.rag.pg_graph import PgGraphStore
        from app.infra.rag.tokenizer import BgeTokenizer
        from app.workers.ingest import WorkerCtx, build_parsers, process_job

        settings = Settings(
            _env_file=None, DATABASE_URL=ctx["settings"].DATABASE_URL,
            DB_POOL_MIN=1, DB_POOL_MAX=5,
            RAG_STORAGE_DIR=str(ctx["blob_root"]), GRAPH_ENABLED=False,
            GRAPH_EXTRACTOR_MODEL="stub-deterministik")
        parsers, ocr = build_parsers(settings)
        nocfg = WorkerCtx(
            settings=settings, pool=pool, worker_id="g08s", embed=ctx["embed"],
            tok=BgeTokenizer(cache_dir=".hf-cache"), qdrant=None,
            cache=None, parsers=parsers, ocr=ocr, extractor=None)
        job = await repo.create_job(pool, workspace=workspace,
                                    document_id=str(ctx["doc"]["id"]))
        claimed = await repo.claim_job(pool, worker_id="g08s")
        assert claimed is not None and claimed["id"] == job["id"]
        assert await process_job(nocfg, claimed) == "done"
        info = await PgGraphStore(pool, workspace=workspace).graph_info()
        assert (info["entity_count"], info["edge_count"]) == (0, 0)
    finally:
        await ctx["embed"].aclose()
        workspace = ctx["workspace"]
        pool = ctx["pool"]
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
        finally:
            await close_pool(pool)
        shutil.rmtree(ctx["blob_root"] / workspace, ignore_errors=True)


@pytest.mark.asyncio
async def test_live_graph_on_tak_ganggu_vector(db_url, embed_url, tag):
    """Smoke F2: ingest graph-ON → dense search vector tetap benar."""
    import shutil

    from app.infra.postgres import close_pool
    from app.infra.rag.pgvector import PgVectorStore

    ctx = await _run_job(db_url, embed_url, tag, enabled=True,
                         workspace_suffix="g08v")
    try:
        store = PgVectorStore(ctx["pool"], workspace=ctx["workspace"])
        qv = (await ctx["embed"].embed_texts(["siapa memasok kopi?"]))[0]
        hits = await store.search_dense(qv, k=3)
        assert hits and "Sumber Rasa" in hits[0]["content"]
    finally:
        await ctx["embed"].aclose()
        workspace = ctx["workspace"]
        pool = ctx["pool"]
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
        finally:
            await close_pool(pool)
        shutil.rmtree(ctx["blob_root"] / workspace, ignore_errors=True)
