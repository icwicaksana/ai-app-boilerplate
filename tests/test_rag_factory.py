"""Unit factory RAG (fake port, tanpa I/O)."""

import pytest

from app.core.config import Settings
from app.core.errors import EmbeddingUnavailableError, RagBackendMismatchError
from app.infra.rag.factory import RagClients, init_rag, retriever_for_tenant
from app.rag.pipeline import HybridRetriever


def _settings(**kw):
    base = {"_env_file": None, "DATABASE_URL": "postgresql://x",
            "PHOENIX_ENABLED": False}
    base.update(kw)
    return Settings(**base)


class _FakeEmbed:
    model, dims, version = "m", 4, "t"

    async def embed_texts(self, texts):
        return [[0.0] * 4 for _ in texts]

    async def aclose(self):
        pass


class _FakeRerank:
    model = "rm"

    async def rerank(self, query, documents, *, top_n):
        return [{"index": i, "score": 1.0} for i in range(top_n)]

    async def aclose(self):
        pass


class _FakeTok:
    name = "fake"

    def count(self, text):
        return len(text.split())

    def truncate_words(self, text, max_tokens):
        return " ".join(text.split()[:max_tokens])


def _clients(**kw):
    args = {"embed": _FakeEmbed(), "rerank": _FakeRerank(),
            "tok": _FakeTok(), "qdrant": None, "dims": 4}
    args.update(kw)
    return RagClients(**args)


@pytest.mark.asyncio
async def test_init_rag_none_bila_embed_kosong():
    s = _settings(RAG_EMBED_BASE_URL="")
    assert await init_rag(s, pool=None) is None


def test_retriever_pg_butuh_pool_bukan_qdrant():
    s = _settings(RAG_BACKEND="pgvector")
    r = retriever_for_tenant(s, _clients(), pool=object(), tenant="toko-a")
    assert isinstance(r, HybridRetriever)
    assert r._store.backend_name == "pgvector"


def test_retriever_qdrant_butuh_klien():
    s = _settings(RAG_BACKEND="qdrant", QDRANT_URL="http://q:6333",
                  QDRANT_API_KEY="k" * 32)
    with pytest.raises(RagBackendMismatchError):
        retriever_for_tenant(s, _clients(qdrant=None), pool=object(),
                             tenant="toko-a")


def test_retriever_tanpa_clients_jadi_unavailable():
    s = _settings()
    with pytest.raises(EmbeddingUnavailableError):
        retriever_for_tenant(s, None, pool=object(), tenant="toko-a")


def test_rag_numbers_diteruskan_dari_settings():
    s = _settings(RAG_DENSE_TOP=7, RAG_FINAL_TOP=2, RAG_RRF_K=30)
    r = retriever_for_tenant(s, _clients(), pool=object(), tenant="t")
    assert (r._dense_top, r._final_top, r._rrf_k) == (7, 2, 30)
