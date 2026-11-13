"""Unit HybridRetriever (D1/D5): RRF, truncasi, floor, degradasi. Fake port, tanpa I/O."""

import pytest

from app.rag.pipeline import HybridRetriever, rrf_fuse, truncate_sentences


def _chunk(cid: str, content: str = "isi") -> dict:
    return {"chunk_id": cid, "doc_id": "d", "content": content, "source": "s",
            "page": None, "score": 0.0, "backend": "fake", "metadata": {}}


class FakeTok:
    name = "fake-ws"

    def count(self, text: str) -> int:
        return len(text.split())

    def truncate_words(self, text: str, max_tokens: int) -> str:
        return " ".join(text.split()[:max_tokens])


class FakeEmbed:
    model = "fake-embed"
    dims = 3
    version = "t1"

    async def embed_texts(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


class FakeStore:
    backend_name = "fake"

    def __init__(self, dense, lexical):
        self._dense = dense
        self._lexical = lexical
        self.seen_filters = []

    async def upsert_chunks(self, *, doc_id, chunks):
        return len(chunks)

    async def search_dense(self, vector, *, k, filters=None):
        self.seen_filters.append(filters)
        return self._dense[:k]

    async def search_lexical(self, query, *, k, filters=None,
                             raw_query=None):
        self.seen_filters.append(filters)
        self.seen_query = query
        self.seen_raw = raw_query
        return self._lexical[:k]

    async def delete_document(self, *, doc_id):
        return 0

    async def index_info(self):
        return {"backend": "fake", "embedding_model": "m",
                "embedding_dim": 3, "embedding_version": "t1",
                "chunk_count": 0}


class FakeRerank:
    model = "fake-rerank"

    def __init__(self, scores=None, timeout=False):
        self._scores = scores
        self._timeout = timeout
        self.seen_docs = None

    async def rerank(self, query, documents, *, top_n):
        self.seen_docs = list(documents)
        if self._timeout:
            raise TimeoutError("rerank lambat")
        scores = self._scores
        if scores is None:
            scores = [1.0 - i * 0.1 for i in range(len(documents))]
        order = sorted(range(len(documents)), key=lambda i: -scores[i])
        return [{"index": i, "score": scores[i]} for i in order[:top_n]]


def _retriever(**kw):
    dense = [_chunk(f"d{i}") for i in range(50)]
    lex = [_chunk(f"d{i}") for i in range(25, 75)]
    kw.setdefault("store", FakeStore(dense, lex))
    kw.setdefault("embed", FakeEmbed())
    kw.setdefault("rerank", FakeRerank())
    kw.setdefault("tok", FakeTok())
    return HybridRetriever(**kw)


class _UnsortedRerank(FakeRerank):
    async def rerank(self, query, documents, *, top_n):
        rows = await super().rerank(query, documents, top_n=top_n)
        return list(reversed(rows))  # provider nakal: tak terurut


class _CorruptRerank(FakeRerank):
    async def rerank(self, query, documents, *, top_n):
        return [{"index": 9999, "score": 0.99},
                {"index": -1, "score": 0.98}]


# --- RRF ---

def test_rrf_urut_dan_deterministik():
    dense = [_chunk("a"), _chunk("b"), _chunk("c")]
    lex = [_chunk("b"), _chunk("c"), _chunk("d")]
    out1 = rrf_fuse(dense, lex, k=60, top_n=4)
    out2 = rrf_fuse(dense, lex, k=60, top_n=4)
    # b & c muncul di dua sisi → di atas a & d.
    assert [c["chunk_id"] for c in out1][:2] == ["b", "c"]
    assert [c["chunk_id"] for c in out1] == [c["chunk_id"] for c in out2]
    assert all(c["score"] > 0 for c in out1)


def test_rrf_top_n_dibatasi():
    dense = [_chunk(f"x{i}") for i in range(10)]
    assert len(rrf_fuse(dense, [], k=60, top_n=3)) == 3


def test_rrf_kosong():
    assert rrf_fuse([], [], k=60, top_n=20) == []


# --- Truncasi ---

def test_truncate_di_batas_kalimat():
    tok = FakeTok()
    text = "Satu dua tiga. Empat lima enam tujuh. Delapan sembilan."
    out = truncate_sentences(text, 6, tok)
    assert out == "Satu dua tiga."
    assert tok.count(out) <= 6


def test_truncate_teks_pendek_utuh():
    tok = FakeTok()
    assert truncate_sentences("Halo dunia.", 100, tok) == "Halo dunia."


def test_truncate_fallback_kata():
    tok = FakeTok()
    out = truncate_sentences("satu dua tiga empat lima", 3, tok)
    assert out == "satu dua tiga"


# --- Pipeline ---

@pytest.mark.asyncio
async def test_search_jalur_bahagia_top5():
    r = _retriever()
    res = await r.search("berapa denda?", k=5)
    assert res["status"] == "ok"
    assert len(res["chunks"]) == 5
    assert res["degraded"] == []


@pytest.mark.asyncio
async def test_search_teruskan_filters_ke_store():
    store = FakeStore([_chunk("a")], [_chunk("a")])
    r = _retriever(store=store)
    await r.search("q", filters={"doc_type": "sop"})
    assert store.seen_filters == [{"doc_type": "sop"}, {"doc_type": "sop"}]


@pytest.mark.asyncio
async def test_search_kosong_jadi_no_evidence():
    r = _retriever(store=FakeStore([], []))
    res = await r.search("q")
    assert res["status"] == "no_evidence"
    assert res["chunks"] == []


@pytest.mark.asyncio
async def test_score_floor_di_bawah_semua_jadi_no_evidence():
    rr = FakeRerank(scores=[0.1] * 20)
    r = _retriever(rerank=rr)
    r._score_floor = 0.5
    res = await r.search("q")
    assert res["status"] == "no_evidence"
    assert res["chunks"] == []


@pytest.mark.asyncio
async def test_rerank_timeout_degradasi_rrf():
    rr = FakeRerank(timeout=True)
    r = _retriever(rerank=rr)
    res = await r.search("q", k=5)
    assert res["status"] == "ok"
    assert res["degraded"] == ["rerank_unavailable"]
    assert len(res["chunks"]) == 5


@pytest.mark.asyncio
async def test_dokumen_rerank_dipotong_doc_budget():
    long = "kalimat. " * 500
    store = FakeStore([_chunk("a", long)], [_chunk("a", long)])
    rr = FakeRerank()
    r = _retriever(store=store, rerank=rr)
    await r.search("q pendek")
    assert rr.seen_docs is not None
    assert FakeTok().count(rr.seen_docs[0]) <= 384


@pytest.mark.asyncio
async def test_rerank_tak_terurut_tetap_benar():
    r = _retriever(rerank=_UnsortedRerank())
    res = await r.search("q")
    scores = [c["score"] for c in res["chunks"]]
    assert scores == sorted(scores, reverse=True)
    assert res["status"] == "ok"


@pytest.mark.asyncio
async def test_rerank_korup_raise_bukan_no_evidence():
    r = _retriever(rerank=_CorruptRerank())
    with pytest.raises(ValueError, match="korup"):
        await r.search("q")


@pytest.mark.asyncio
async def test_trace_lengkap_jalur_ok():
    r = _retriever()
    res = await r.search("berapa hari retur?")
    t = res["trace"]
    assert t["backend"] == "fake"
    assert set(t["timings_ms"]) == {"embed", "dense", "lexical", "rerank",
                                    "total"}
    assert all(v >= 0 for v in t["timings_ms"].values())
    c = t["counts"]
    assert c["dense"] == 50 and c["lexical"] == 50
    assert c["candidates"] == 20 and c["final"] == 5
    assert c["ranked"] == 20
    assert t["top_score"] > 0 and t["floor"] == 0.05
    assert t["status"] == "ok" and t["degraded"] == []
    assert t["query_preview"] == "berapa hari retur?"


@pytest.mark.asyncio
async def test_trace_ada_saat_no_evidence_dan_degraded():
    r = _retriever(rerank=FakeRerank(scores=[0.0] * 20))
    res = await r.search("jadwal kereta?")
    assert res["status"] == "no_evidence"
    assert res["trace"]["status"] == "no_evidence"
    assert res["trace"]["counts"]["final"] == 0
    r2 = _retriever(rerank=FakeRerank(timeout=True))
    res2 = await r2.search("q?")
    assert res2["degraded"] == ["rerank_unavailable"]
    assert res2["trace"]["degraded"] == ["rerank_unavailable"]
    assert res2["trace"]["counts"]["final"] > 0  # fallback RRF terisi


@pytest.mark.asyncio
async def test_lexical_menerima_query_ternormalisasi():
    dense = [_chunk(f"d{i}") for i in range(50)]
    lex = [_chunk(f"d{i}") for i in range(25, 75)]
    store = FakeStore(dense, lex)
    r = _retriever(store=store)
    await r.search("Berapa lama garansi laptop?")
    # Stopword 'berapa' hilang, imbuhan di-stem — dense tak tersentuh
    # (embed menerima query mentah; hanya lexical dinormalisasi).
    assert store.seen_query == "lama garansi laptop"
    assert store.seen_raw == "Berapa lama garansi laptop?"
    r2 = _retriever(store=FakeStore(dense, lex), stem=False)
    await r2.search("Barang diretur?")
    assert r2._store.seen_query == "barang diretur"
