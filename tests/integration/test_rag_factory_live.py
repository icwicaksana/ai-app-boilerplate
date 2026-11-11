"""Live factory RAG: init penuh + tokenizer + validasi triples. Skip bila infra mati."""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_init_rag_penuh(db_url, embed_url):
    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.factory import init_rag, retriever_for_tenant
    from app.rag.pipeline import HybridRetriever

    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_EMBED_BASE_URL=embed_url)
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        clients = await init_rag(settings, pool)
        assert clients is not None
        assert clients.dims == 1024
        assert clients.qdrant is None  # default pgvector
        assert clients.tok.count("Denda 2% per bulan.") > 3
        r = retriever_for_tenant(settings, clients, pool, tenant="it-factory")
        assert isinstance(r, HybridRetriever)
        await clients.aclose()
    finally:
        await close_pool(pool)


@pytest.mark.asyncio
async def test_live_tokenizer_bge_id(db_url):
    from app.core.config import Settings
    from app.infra.rag.tokenizer import BgeTokenizer

    s = Settings(_env_file=None, DATABASE_URL=db_url)
    _ = s  # konsistensi fixture (db menyiratkan lingkungan dev siap)
    tok = BgeTokenizer("BAAI/bge-m3", cache_dir=".hf-cache")
    assert tok.count("") == 0
    n = tok.count("Pengembalian barang maksimal 14 hari.")
    assert 3 < n < 30, n
    long = "kata " * 200
    assert tok.count(tok.truncate_words(long, 10)) <= 10
