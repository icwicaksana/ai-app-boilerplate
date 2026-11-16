"""Live ingest layered nyata (L1+L2 GLiNER, tanpa L3). ~1-2 mnt (load model)."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_ingest_layered_l1_l2(db_url, embed_url, tag):
    from app.core.config import Settings
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.extract.gliner import GlierExtractor
    from app.infra.rag.extract.l1_rules import L1RulesExtractor
    from app.infra.rag.extract.layered import LayeredExtractor
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.pg_graph import PgGraphStore
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.infra.rag_migrate import apply_rag_migration
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    cache = os.environ.get("HF_HUB_CACHE", ".hf-cache")
    if not os.path.isdir(os.path.join(
            cache, "models--fastino--gliner2.5-multi-v1")):
        pytest.skip("model GLiNER2.5 belum diunduh")
    tenant = f"it-g08y-{tag}"
    blob_root = Path(".test-blobs").resolve()
    settings = Settings(
        _env_file=None, DATABASE_URL=db_url, DB_POOL_MIN=1, DB_POOL_MAX=5,
        RAG_STORAGE_DIR=str(blob_root), GRAPH_ENABLED=True,
        GRAPH_EXTRACTOR_MODEL="fastino/gliner2.5-multi-v1")
    pool = create_pool(settings)
    await open_pool(pool)
    await apply_rag_migration(pool)
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    try:
        blob = ("PT Sumber Rasa memasok Kopi Arabika.\n\n"
                "Budi Hartono bekerja sebagai manajer di PT Sumber Rasa.\n"
                ).encode()
        rel = f"{tenant}/doc.bin"
        target = blob_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        doc = await repo.create_document(
            pool, tenant=tenant, source_type="md", source_uri=rel,
            title="layered.md", lang="id", size_bytes=len(blob),
            sha256=hashlib.sha256(blob + tenant.encode()).hexdigest(),
            parser="auto", chunk_preset="markdown", created_by="g08y")
        job = await repo.create_job(pool, tenant=tenant,
                                    document_id=str(doc["id"]))
        claimed = await repo.claim_job(pool, worker_id="g08y")
        parsers, ocr = build_parsers(settings)
        extractor = LayeredExtractor(
            L1RulesExtractor(), GlierExtractor(cache_dir=cache), None)
        ctx = WorkerCtx(
            settings=settings, pool=pool, worker_id="g08y", embed=embed,
            tok=BgeTokenizer(cache_dir=".hf-cache"), qdrant=None,
            cache=None, parsers=parsers, ocr=ocr, extractor=extractor)
        assert await process_job(ctx, claimed) == "done"
        doc2 = await repo.get_document(pool, tenant=tenant,
                                       document_id=str(doc["id"]))
        assert doc2["meta"]["graph_status"] == "ok"
        assert doc2["meta"]["graph_edges"] >= 2
        store = PgGraphStore(pool, tenant=tenant)
        hits = await store.expand(["budi hartono"], k=5)
        assert hits, "L2 tak mengekstrak Budi Hartono"
        assert any(h["metadata"].get("layers") == ["L2"] or "L2" in (
            h["metadata"].get("layers") or []) for h in hits)
    finally:
        await embed.aclose()
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE tenant = %s", (tenant,))
        finally:
            await close_pool(pool)
        shutil.rmtree(blob_root / tenant, ignore_errors=True)
