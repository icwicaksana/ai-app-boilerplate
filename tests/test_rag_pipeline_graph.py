"""Unit F3: seed, RRF 3-way, pipeline graph, sitasi, grounding G1-G3."""

import pytest

from app.rag import citations as C
from app.rag.grounding import (
    check_graph_provenance,
    expand_graph_support,
    weak_graph_singleton,
)
from app.rag.kg_seed import (
    preseed_aliases,
    seed_candidates,
    seed_candidates_weighted,
)
from app.rag.pipeline import HybridRetriever, rrf_fuse


def _c(cid, score=0.9, **kw):
    d = {"chunk_id": cid, "doc_id": "d1", "content": f"isi {cid}",
         "source": "s.md", "page": None, "score": score,
         "backend": "pgvector", "metadata": {}}
    d.update(kw)
    return d


def _hit(cid, score=0.8, hops=1, prov=("c1",), conf=0.9, layers=("L2",)):
    return {"chunk_id": cid, "doc_id": "d1", "content": f"verbal {cid}",
            "source": "s.md", "page": None, "score": score, "hops": hops,
            "metadata": {"kind": "graph", "provenance": list(prov),
                         "confidence": conf, "layers": list(layers),
                         "relations": ["memasok"], "edge_ids": [1],
                         "extractor": "stub", "onto_version": "kg_onto/v1"}}


class _Tok:
    def count(self, t):
        return max(1, len((t or "").split()))

    def truncate_words(self, t, n):
        return " ".join((t or "").split()[:n])


class _Embed:
    async def embed_texts(self, texts):
        return [[0.1] for _ in texts]


class _Rerank:
    def __init__(self, scores):
        self._scores = scores

    async def rerank(self, q, docs, top_n=None):
        return [{"index": i, "score": s}
                for i, s in enumerate(self._scores[:len(docs)])]


class _Store:
    backend_name = "pgvector"

    def __init__(self, dense, lex):
        self._dense, self._lex = dense, lex

    async def search_dense(self, qv, k=50, filters=None):
        return self._dense

    async def search_lexical(self, q, k=50, filters=None, raw_query=""):
        return self._lex


class _Graph:
    backend_name = "pggraph"

    def __init__(self, hits=(), exc=None, matched=None):
        self._hits, self._exc = list(hits), exc
        self._matched = matched
        self.calls = []

    async def resolve_seeds(self, seeds):
        if self._exc is not None:
            raise self._exc
        if self._matched is not None:
            return list(self._matched)
        return list(seeds)

    async def list_aliases(self):
        return {}

    async def upsert_aliases(self, aliases):
        return len(aliases)

    async def expand(self, seeds, *, k, filters=None, max_hops=2,
                     fanout=50):
        self.calls.append({"seeds": seeds, "k": k, "filters": filters,
                           "max_hops": max_hops, "fanout": fanout})
        if self._exc is not None:
            raise self._exc
        return list(self._hits)


def test_seed_prioritas_dan_alias():
    out = seed_candidates('berapa harga "Kopi Arabika" di Bandung?')
    assert out[0] == "kopi arabika"  # quote dulu
    assert "bandung" in out
    out2 = seed_candidates("kopi arabika", {"kopi arabika": "kopi"})
    assert out2[0] == "kopi"
    assert seed_candidates("") == []


def test_seed_lowercase_token_dan_batas_fullquery():
    out = seed_candidates("komputer jinjing")
    assert "komputer jinjing" in out  # bigram (alias multi-kata)
    assert "komputer" in out and "jinjing" in out
    w = dict(seed_candidates_weighted("komputer jinjing"))
    assert w["komputer jinjing"] == 0.8 > w["komputer"] == 0.6
    long_q = "kata " * 20 + "ekor?"
    out_long = seed_candidates(long_q)
    assert all(len(s.split()) <= 8 for s in out_long)  # full-query ditolak
    assert "ekor" in out_long  # token tetap ada


def test_rrf_3way_dan_2way_identik():
    d, lx = [_c("a"), _c("b")], [_c("b"), _c("c")]
    two = rrf_fuse(d, lx)
    three_empty = rrf_fuse(d, lx, [])
    assert [c["chunk_id"] for c in two] == [c["chunk_id"] for c in three_empty]
    g = [_c("g1", kind="graph")]
    three = rrf_fuse(d, lx, g)
    assert "g1" in [c["chunk_id"] for c in three]


def _retriever(dense, lex, scores, graph=None, **kw):
    args = {"embed": _Embed(), "rerank": _Rerank(scores),
            "store": _Store(dense, lex), "tok": _Tok(),
            "score_floor": 0.05, "graph": graph}
    args.update(kw)
    return HybridRetriever(**args)


@pytest.mark.asyncio
async def test_pipeline_graph_masuk_dan_trace():
    g = _Graph([_hit("g1"), _hit("g2"), _hit("g3")])
    r = _retriever([_c("a")], [_c("b")], [0.9] * 10, graph=g)
    res = await r.search("Kopi Arabika?")
    ids = [c["chunk_id"] for c in res["chunks"]]
    assert {"g1", "g2", "g3"} <= set(ids)  # cap default 6
    assert res["trace"]["graph_counts"]["kept"] == 3
    assert res["trace"]["seed_confidence"] == 0.9  # caps-tier match
    assert res["trace"]["floors"] == {"natural": 0.05, "graph": 0.10}
    assert g.calls[0]["filters"] is None
    assert g.calls[0]["max_hops"] == 1  # hemat: hop-1 cukup (≥3 path)


@pytest.mark.asyncio
async def test_pipeline_cap_adaptif_sepakat():
    g = _Graph([_hit(f"g{i}") for i in range(8)])
    r = _retriever([_c("a")], [_c("a")], [0.9] * 12, graph=g)
    res = await r.search("Kopi?")
    assert res["trace"]["graph_counts"]["cap"] == 2  # top-1 sepakat
    assert res["trace"]["graph_counts"]["kept"] == 2


@pytest.mark.asyncio
async def test_pipeline_hop2_bila_kurus_dan_strip_filter():
    g = _Graph([_hit("g1")])
    r = _retriever([_c("a")], [_c("b")], [0.9] * 10, graph=g)
    res = await r.search("Kopi?", filters={"access_level": "public",
                                           "doc_type": "x"})
    assert [c["max_hops"] for c in g.calls] == [1, 2]  # kurus → hop-2
    # v1.0 G6: access_level dinormalisasi ke list (IN), kunci lain strip.
    assert g.calls[0]["filters"] == {"access_level": ["public"]}  # strip


@pytest.mark.asyncio
async def test_pipeline_graph_gagal_degraded_bukan_500():
    g = _Graph(exc=TimeoutError("boom"))
    r = _retriever([_c("a")], [_c("b")], [0.9, 0.8], graph=g)
    res = await r.search("Kopi?")
    assert "graph_unavailable" in res["degraded"]
    assert res["status"] == "ok"
    assert [c["chunk_id"] for c in res["chunks"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_pipeline_floor_ganda():
    g = _Graph([_hit("g1")])
    # RRF: a(dense1+lex?) … skor rerank: natural 0.9/0.8, graph 0.06.
    r = _retriever([_c("a"), _c("b")], [_c("a"), _c("b")],
                   [0.9, 0.8, 0.06], graph=g, graph_score_floor=0.5)
    res = await r.search("Kopi?")
    assert "g1" not in [c["chunk_id"] for c in res["chunks"]]  # < 0.5
    r2 = _retriever([_c("a"), _c("b")], [_c("a"), _c("b")],
                    [0.9, 0.8, 0.06], graph=g, graph_score_floor=0.05)
    res2 = await r2.search("Kopi?")
    assert "g1" in [c["chunk_id"] for c in res2["chunks"]]  # ≥ 0.05


@pytest.mark.asyncio
async def test_pipeline_confidence_rendah_skip_expand():
    g = _Graph([_hit("g1")], matched=["kopi"])  # 1 dari N kandidat
    r = _retriever([_c("a")], [_c("b")], [0.9] * 10, graph=g,
                   graph_seed_confidence=0.9)
    res = await r.search("Kopi Arabika Bandung Jakarta?")
    assert g.calls == []  # di bawah threshold → expand tak dipanggil
    assert res["trace"]["seed_confidence"] < 0.9
    assert "graph_unavailable" not in res["degraded"]  # sunyi, bukan error


@pytest.mark.asyncio
async def test_pipeline_degraded_floor_skip_tertanda():
    from app.core.errors import RerankError

    class _BadRerank:
        async def rerank(self, q, docs, top_n=None):
            raise RerankError("down")

    r = HybridRetriever(embed=_Embed(), rerank=_BadRerank(),
                        store=_Store([_c("a")], [_c("b")]), tok=_Tok())
    res = await r.search("Kopi?")
    assert res["trace"].get("floor_skipped") is True
    assert [c["chunk_id"] for c in res["chunks"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_pipeline_tanpa_graph_tanpa_kunci():
    r = _retriever([_c("a")], [_c("b")], [0.9, 0.8])
    res = await r.search("Kopi?")
    assert "graph_timings_ms" not in res["trace"]
    assert res["degraded"] == []


def test_sitasi_graph_dan_render():
    chunks = [_c("a"), _c("g1", kind="graph", provenance=["c1", "c2"],
                     metadata={"provenance": ["c1", "c2"], "confidence": 0.9,
                               "extractor": "stub", "relations": ["memasok"]})]
    cits = C.build_citations(chunks)
    assert cits[1]["kind"] == "graph"
    assert cits[1]["provenance"] == ["c1", "c2"]
    assert "kind" not in cits[0]
    assert C.render_sources(cits).splitlines()[1].endswith("(+1 bukti)")


def _gfull(cid="g1", prov=("c1",), rids=("r1",), texts=("isi",),
             conf=0.9, layers=("L2",)):
    return _c(cid, kind="graph", provenance=list(prov),
              metadata={"provenance": list(prov),
                        "chunk_row_ids": list(rids),
                        "provenance_contents": list(texts),
                        "confidence": conf, "layers": list(layers)})


def test_rrf_tiebreak_graph_deterministik():
    # Skor RRF seri 3 arah → (-skor, rank_dense, rank_lex, rank_graph, id).
    d, lx = [_c("a"), _c("b")], [_c("b"), _c("a")]
    g = [_c("a", kind="graph"), _c("b", kind="graph")]
    once = [c["chunk_id"] for c in rrf_fuse(d, lx, g)]
    twice = [c["chunk_id"] for c in rrf_fuse(d, lx, g)]
    assert once == twice == ["a", "b"]  # seri penuh → id men stabilization
    # Graph-only candidate keeps its kind through fuse.
    g2 = [_c("z", kind="graph")]
    fused = rrf_fuse(d, lx, g2)
    assert next(c for c in fused if c["chunk_id"] == "z")["kind"] == "graph"


def test_finalize_g3_refusal_singleton_lemah():
    from app.agents.RagAnswerAgent import RagAnswerAgent

    agent = object.__new__(RagAnswerAgent)  # finalize murni-state
    weak = _gfull(cid="g9", prov=("c9",), rids=("r9",),
                  texts=("verbal tanpa angka",), conf=0.5)
    weak["content"] = "verbal tanpa angka"
    state = {"chunks": [weak], "chunks_included": [weak],
             "draft": "Harganya 50000.[^1]",
             "retrieval_status": "ok", "degraded": [],
             "retrieval_trace": {}}
    out = agent._node_finalize(state)
    assert out["structured"]["refusal"] is True  # angka tanpa dukungan

    strong = _gfull(cid="g8", prov=("c8",), rids=("r8",),
                    texts=("harga 50000 di nota",), conf=0.5)
    strong["content"] = "toko menjual barang"
    state2 = {"chunks": [strong], "chunks_included": [strong],
              "draft": "Harganya 50000.[^1]",
              "retrieval_status": "ok", "degraded": [],
              "retrieval_trace": {}}
    out2 = agent._node_finalize(state2)
    # Angka didukung provenance tapi singleton lemah → tetap refusal (G3).
    assert out2["structured"]["refusal"] is True

    l1 = _gfull(cid="g7", prov=("c7",), rids=("r7",),
                texts=("harga 50000 pasti",), conf=0.5, layers=("L1",))
    l1["content"] = "toko menjual barang"
    state3 = {"chunks": [l1], "chunks_included": [l1],
              "draft": "Harganya 50000.[^1]",
              "retrieval_status": "ok", "degraded": [],
              "retrieval_trace": {}}
    out3 = agent._node_finalize(state3)
    assert out3["structured"]["refusal"] is False  # L1 exempt
    assert out3["structured"]["citations"][0]["kind"] == "graph"


def test_preseed_alias_longest_first():
    al = {"komputer jinjing": "laptop thinkpad", "ess": "menu es kopi susu"}
    assert preseed_aliases("Komputer jinjing dipakai di mana?",
                           al) == ["laptop thinkpad"]
    assert preseed_aliases("siapa memasok bahan ess?", al) == [
        "menu es kopi susu"]
    assert preseed_aliases("teks tanpa alias", al) == []
    assert preseed_aliases("q", {}) == []


@pytest.mark.asyncio
async def test_pipeline_preseed_masuk_expand():
    class _GAlias(_Graph):
        async def list_aliases(self):
            return {"ess": "menu es kopi susu"}

        async def resolve_seeds(self, seeds):
            return [s for s in seeds if s == "menu es kopi susu"]

    g = _GAlias([_hit("g1")])
    r = _retriever([_c("a")], [_c("b")], [0.9] * 10, graph=g)
    res = await r.search("siapa memasok bahan ess?")
    assert g.calls and g.calls[0]["seeds"] == ["menu es kopi susu"]
    assert res["trace"]["seed_confidence"] == 1.0
    assert res["trace"]["graph_counts"]["kept"] == 1


@pytest.mark.asyncio
async def test_pipeline_rerank_query_expanded():
    seen: dict = {}

    class _RecRerank:
        async def rerank(self, q, docs, top_n=None):
            seen["q"] = q
            return [{"index": i, "score": 0.9} for i in range(len(docs))]

    r = HybridRetriever(embed=_Embed(), rerank=_RecRerank(),
                        store=_Store([_c("a")], [_c("b")]), tok=_Tok(),
                        graph=_Graph([_hit("g1")]))
    await r.search("Kopi Arabika?")
    # Seed kanonis tertempel di query rerank (jembatan leksikal).
    assert "kopi arabika" in seen["q"].lower()
    r2 = HybridRetriever(embed=_Embed(), rerank=_RecRerank(),
                         store=_Store([_c("a")], [_c("b")]), tok=_Tok())
    await r2.search("Kopi Arabika?")
    assert seen["q"] == "Kopi Arabika?"  # tanpa graph: verbatim


def test_g1_provenance_struktural():
    assert check_graph_provenance([_gfull()]) == []  # utuh
    g = _gfull(prov=(), rids=(), texts=())
    assert check_graph_provenance([g]) != []  # tanpa provenance
    g2 = _gfull(rids=())
    assert check_graph_provenance([g2]) != []  # tanpa row id
    g3 = _gfull(texts=("",))
    assert check_graph_provenance([g3]) != []  # isi hilang
    assert check_graph_provenance([_c("a")]) == []  # vector skip


def test_g2_union_dan_g3_singleton():
    g = _gfull(texts=("harga 50000",), conf=0.5)
    assert expand_graph_support(g, {}) == [g["content"], "harga 50000"]
    # Fallback by_id bila lampiran absen.
    g4 = _c("g4", kind="graph", provenance=["c1"],
            metadata={"provenance": ["c1"]})
    by_id = {"c1": dict(_c("c1"), content="harga 50000")}
    assert expand_graph_support(g4, by_id)[1] == "harga 50000"
    assert weak_graph_singleton(g) is True
    g2 = _c("g2", kind="graph", provenance=["c1", "c2"],
            metadata={"provenance": ["c1", "c2"], "confidence": 0.5,
                      "layers": ["L2"]})
    assert weak_graph_singleton(g2) is False  # multi-hop bukan singleton
    g3 = _c("g3", kind="graph", provenance=["c1"],
            metadata={"provenance": ["c1"], "confidence": 0.5,
                      "layers": ["L1"]})
    assert weak_graph_singleton(g3) is False  # L1 tak pernah lemah
    assert weak_graph_singleton(_c("a")) is False
