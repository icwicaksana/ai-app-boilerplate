"""Live E2E retrieval graph-ON: ingest → retriever → sitasi graph."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_retrieval_graph_on(db_url, embed_url, tag):
    from app.core.config import Settings
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.extract.stub import StubExtractor
    from app.infra.rag.factory import init_rag, retriever_for_workspace
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.infra.rag_migrate import apply_rag_migration
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    workspace = f"it-g08r-{tag}"
    blob_root = Path(".test-blobs").resolve()
    settings = Settings(
        _env_file=None, DATABASE_URL=db_url, DB_POOL_MIN=1, DB_POOL_MAX=5,
        RAG_STORAGE_DIR=str(blob_root), GRAPH_ENABLED=True,
        GRAPH_EXTRACTOR_MODEL="stub-deterministik")
    pool = create_pool(settings)
    await open_pool(pool)
    await apply_rag_migration(pool)
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    try:
        blob = ("PT Sumber Rasa memasok Kopi Arabika.\n\n"
                "Kopi Arabika dipakai di Menu Es Kopi Susu.\n").encode()
        rel = f"{workspace}/doc.bin"
        target = blob_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        doc = await repo.create_document(
            pool, workspace=workspace, source_type="md", source_uri=rel,
            title="resto.md", lang="id", size_bytes=len(blob),
            sha256=hashlib.sha256(blob + workspace.encode()).hexdigest(),
            parser="auto", chunk_preset="markdown", created_by="g08r")
        job = await repo.create_job(pool, workspace=workspace,
                                    document_id=str(doc["id"]))
        claimed = await repo.claim_job(pool, worker_id="g08r")
        parsers, ocr = build_parsers(settings)
        ctx = WorkerCtx(
            settings=settings, pool=pool, worker_id="g08r", embed=embed,
            tok=BgeTokenizer(cache_dir=".hf-cache"), qdrant=None,
            cache=None, parsers=parsers, ocr=ocr,
            extractor=StubExtractor())
        assert await process_job(ctx, claimed) == "done"

        clients = await init_rag(settings, pool)
        assert clients is not None and clients.extractor is not None
        try:
            retr = retriever_for_workspace(settings, clients, pool,
                                        workspace=workspace)
            res = await retr.search("PT Sumber Rasa memasok apa?")
            kinds = [c.get("kind") or "vector" for c in res["chunks"]]
            assert "graph" in kinds
            g = next(c for c in res["chunks"]
                     if (c.get("kind") or "") == "graph")
            assert g["provenance"] and g["metadata"]["relations"]
            assert res["trace"]["graph_counts"]["kept"] >= 1
            assert "graph_unavailable" not in res["degraded"]
            # Sitasi graph ter-render (+N bukti bila 2-hop).
            from app.rag import citations as C

            cits = C.build_citations(res["chunks"])
            assert any(c.get("kind") == "graph" for c in cits)
        finally:
            await clients.aclose()
    finally:
        await embed.aclose()
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
        finally:
            await close_pool(pool)
        shutil.rmtree(blob_root / workspace, ignore_errors=True)
