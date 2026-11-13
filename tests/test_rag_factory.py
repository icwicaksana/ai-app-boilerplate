"""Unit factory RAG (fake port, tanpa I/O)."""

import httpx
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


def _ok_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/embeddings":
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] * 4}]})
        raise AssertionError(request.url.path)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_init_rag_tolak_fallback_beda_model():
    s = _settings(RAG_EMBED_BASE_URL="http://primer",
                  RAG_EMBED_MODEL="BAAI/bge-m3",
                  RAG_RERANK_BASE_URL="http://primer",
                  RAG_EMBED_FALLBACK_URL="http://cadangan",
                  RAG_EMBED_FALLBACK_MODEL="lain/model")
    with pytest.raises(RuntimeError, match="beda-model"):
        await init_rag(s, pool=None, transport=_ok_transport())


@pytest.mark.asyncio
async def test_init_rag_fallback_down_tetap_lanjut_tanpa_wrapper(
        monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        # Primer sehat; host 'cadangan' selalu 500.
        if request.url.host == "cadangan":
            return httpx.Response(500, json={})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/embeddings":
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] * 4}]})
        raise AssertionError(request.url.path)

    class _FakePool:
        def connection(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return self

        async def fetchall(self):
            return []  # index kosong → triples cocok

    monkeypatch.setattr("app.infra.rag.factory.BgeTokenizer",
                        lambda *a, **k: _FakeTok())

    from app.infra.rag.infinity import (
        FallbackEmbeddings,
        InfinityEmbeddings,
    )

    s = _settings(RAG_EMBED_BASE_URL="http://primer",
                  RAG_EMBED_MODEL="BAAI/bge-m3",
                  RAG_RERANK_BASE_URL="http://primer",
                  RAG_EMBED_FALLBACK_URL="http://cadangan",
                  RAG_EMBED_FALLBACK_MODEL="BAAI/bge-m3")
    clients = await init_rag(s, pool=_FakePool(),
                             transport=httpx.MockTransport(handler))
    try:
        assert type(clients.embed) is InfinityEmbeddings  # tanpa wrapper
        assert not isinstance(clients.embed, FallbackEmbeddings)
        assert clients.dims == 4
    finally:
        await clients.aclose()
