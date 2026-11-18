"""v1.0 V1-D/4: ranking lexical Qdrant (G7, BM25-lite client-side)."""

from __future__ import annotations

import pytest

from app.rag.rank import rank_bm25_lite


def test_tf_menang():
    docs = [{"tokens": "denda retur".split()},
            {"tokens": "denda denda denda retur".split()},
            {"tokens": "jadwal kereta".split()}]
    ranked = rank_bm25_lite(["denda", "retur"], docs)
    assert [i for i, _ in ranked] == [1, 0, 2]
    assert ranked[0][1] > ranked[1][1] > ranked[2][1] == 0.0


def test_term_langka_bobot_lebih():
    docs = [{"tokens": "bank umum".split()},
            {"tokens": "bank zirkonia".split()},
            {"tokens": "bank umum lagi".split()}]
    ranked = rank_bm25_lite(["bank", "zirkonia"], docs)
    assert ranked[0][0] == 1  # 'zirkonia' langka → doc 1 di atas


def test_pendek_menang_seri_tf():
    docs = [{"tokens": ["kucing"] + ["tikus"] * 20},
            {"tokens": ["kucing"]}]
    ranked = rank_bm25_lite(["kucing"], docs)
    assert ranked[0][0] == 1  # tf sama (1) → dokumen pendek menang


def test_deterministik_dan_kosong():
    docs = [{"tokens": ["a"]}, {"tokens": ["a"]}]
    assert rank_bm25_lite(["a"], docs) == rank_bm25_lite(["a"], docs)
    assert rank_bm25_lite([], docs) == [(0, 0.0), (1, 0.0)]
    assert rank_bm25_lite(["a"], []) == []


@pytest.mark.asyncio
async def test_qdrant_lexical_terurut_relevansi():
    """search_lexical Qdrant mengurut by skor (bukan doc_id/ord)."""
    from types import SimpleNamespace

    from app.infra.rag.qdrant import QdrantStore

    def _p(pid, doc, ord_, norm):
        return SimpleNamespace(
            id=pid,
            payload={"document_id": doc, "ord": ord_, "content": norm,
                     "content_norm": norm, "meta": {"_chunk_id": pid}},
            score=None)

    points = [_p("p1", "doc-b", 0, "jadwal kereta api"),
              _p("p2", "doc-a", 9, "denda denda denda retur"),
              _p("p3", "doc-a", 1, "denda retur")]

    class _Client:
        async def scroll(self, *a, **k):
            return (points, None)

    store = QdrantStore.__new__(QdrantStore)
    store._client = _Client()
    store._collection = "c"
    store._workspace = "toko-a"
    out = await store.search_lexical("denda retur", k=3)
    assert [c["chunk_id"] for c in out] == ["p2", "p3", "p1"]
    scores = [c["score"] for c in out]
    assert scores[0] > scores[1] > scores[2]
