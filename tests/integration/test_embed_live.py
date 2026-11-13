"""Live service embedding (Infinity). Skip bila service mati.

Kecil & cepat (detik): verifikasi kontrak HTTP, BUKAN kualitas model
(kualitas = eval mini F6)."""

import pytest

pytestmark = pytest.mark.integration

from app.infra.rag.infinity import (FallbackEmbeddings, InfinityEmbeddings,
                                     InfinityReranker)  # noqa: E402


@pytest.mark.asyncio
async def test_live_embed_dim_dan_norma(embed_url):
    c = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024, timeout_ms=60_000)
    try:
        vecs = await c.embed_texts(["Denda 2% per bulan.", "Toko buka jam 8."])
        assert len(vecs) == 2 and all(len(v) == 1024 for v in vecs)
        for v in vecs:
            norm = sum(x * x for x in v) ** 0.5
            assert 0.99 < norm < 1.01, f"vektor tak ternormalisasi: {norm}"
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_live_rerank_top1_masuk_akal(embed_url):
    c = InfinityReranker(embed_url, "BAAI/bge-reranker-v2-m3",
                         timeout_ms=120_000)
    try:
        out = await c.rerank("berapa denda keterlambatan?",
                             ["Denda keterlambatan 2% per bulan dari nilai tagihan.",
                              "Toko buka setiap hari pukul delapan pagi.",
                              "Parkir gratis dua jam untuk pembeli."],
                             top_n=3)
        assert [r["index"] for r in out][0] == 0
        assert all(isinstance(r["score"], float) for r in out)
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_live_fallback_primer_mati(embed_url):
    """Chaos: primer di port mati → fallback nyata melayani (F5b)."""
    primer = InfinityEmbeddings("http://127.0.0.1:9", "BAAI/bge-m3", 1024,
                                timeout_ms=5_000)
    cadangan = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                                  timeout_ms=60_000)
    w = FallbackEmbeddings(primer, cadangan)
    try:
        vecs = await w.embed_texts(["uji failover"])
        assert len(vecs) == 1 and len(vecs[0]) == 1024
        assert w.on_fallback is True
        assert await w.ping() is True
    finally:
        await w.aclose()
