"""Unit klien Infinity (MockTransport, tanpa server)."""

import httpx
import pytest

from app.core.errors import EmbeddingUnavailableError
from app.infra.rag.infinity import (
    InfinityEmbeddings,
    InfinityError,
    InfinityReranker,
)


def _embed_transport(payload=None, status=200, exc=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embeddings"
        if exc is not None:
            raise exc
        return httpx.Response(status, json=payload or {"data": []})
    return httpx.MockTransport(handler)


def _rerank_transport(payload=None, status=200, exc=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rerank"
        if exc is not None:
            raise exc
        return httpx.Response(status, json=payload or {"results": []})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_embed_sukses_kembalikan_vektor():
    tr = _embed_transport({"data": [{"embedding": [0.1, 0.2]},
                                    {"embedding": [0.3, 0.4]}]})
    c = InfinityEmbeddings("http://x", "m", 2, transport=tr)
    assert await c.embed_texts(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert c.model == "m" and c.dims == 2
    await c.aclose()


@pytest.mark.asyncio
async def test_embed_kosong_tanpa_request():
    c = InfinityEmbeddings("http://x", "m", 2,
                           transport=_embed_transport(exc=AssertionError()))
    assert await c.embed_texts([]) == []
    await c.aclose()


@pytest.mark.asyncio
async def test_embed_dim_salah_jadi_unavailable():
    tr = _embed_transport({"data": [{"embedding": [0.1]}]})  # dim 1, harap 2
    c = InfinityEmbeddings("http://x", "m", 2, transport=tr)
    with pytest.raises(EmbeddingUnavailableError):
        await c.embed_texts(["a"])
    await c.aclose()


@pytest.mark.asyncio
async def test_embed_http_error_jadi_unavailable():
    for tr in (_embed_transport(status=500),
               _embed_transport(exc=httpx.ConnectError("down")),
               _embed_transport(exc=httpx.TimeoutException("slow"))):
        c = InfinityEmbeddings("http://x", "m", 2, transport=tr)
        with pytest.raises(EmbeddingUnavailableError):
            await c.embed_texts(["a"])
        await c.aclose()


@pytest.mark.asyncio
async def test_rerank_sukses_parse_relevance():
    tr = _rerank_transport({"results": [{"index": 1, "relevance_score": 0.9},
                                        {"index": 0, "relevance_score": 0.1}]})
    c = InfinityReranker("http://x", "rm", transport=tr)
    out = await c.rerank("q", ["d0", "d1"], top_n=2)
    assert out == [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}]
    await c.aclose()


@pytest.mark.asyncio
async def test_rerank_timeout_jadi_builtin_timeouterror():
    tr = _rerank_transport(exc=httpx.TimeoutException("slow"))
    c = InfinityReranker("http://x", "rm", transport=tr)
    with pytest.raises(TimeoutError):
        await c.rerank("q", ["d"], top_n=1)
    await c.aclose()


@pytest.mark.asyncio
async def test_rerank_error_lain_jadi_rerank_error():
    from app.core.errors import RerankError

    for tr in (_rerank_transport(status=500),
               _rerank_transport(exc=httpx.ConnectError("down")),
               _rerank_transport({"hasil": []})):  # skema asing
        c = InfinityReranker("http://x", "rm", transport=tr)
        with pytest.raises(RerankError):
            await c.rerank("q", ["d"], top_n=1)
        await c.aclose()
