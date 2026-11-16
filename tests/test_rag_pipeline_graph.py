"""Unit F3: seed, RRF 3-way, pipeline graph, sitasi, grounding G1-G3."""

import pytest

from app.rag import citations as C
from app.rag.grounding import (
    check_graph_provenance,
    expand_graph_support,
    weak_graph_singleton,
)
from app.rag.kg_seed import seed_candidates
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

    def __init__(self, hits=(), exc=None):
        self._hits, self._exc = list(hits), exc
        self.calls = []

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
    assert out2 == ["kopi"]
    assert seed_candidates("") == []


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
    assert res["trace"]["seed_confidence"] == 1.0
    assert res["trace"]["floors"] == {"natural": 0.05, "graph": 0.05}
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
    assert g.calls[0]["filters"] == {"access_level": "public"}  # strip


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


def test_g1_provenance():
    g = _c("g1", kind="graph", provenance=["c1", "cX"])
    assert check_graph_provenance([g], {"c1"}) != []  # cX hilang
    assert check_graph_provenance([g], {"c1", "cX"}) == []
    assert check_graph_provenance(
        [_c("g1", kind="graph", provenance=[])], {"c1"}) != []
    assert check_graph_provenance([_c("a")], set()) == []  # vector skip


def test_g2_union_dan_g3_singleton():
    g = _c("g1", kind="graph", provenance=["c1"],
           metadata={"provenance": ["c1"], "confidence": 0.5,
                     "layers": ["L2"]})
    by_id = {"c1": _c("c1")}
    by_id["c1"]["content"] = "harga 50000"
    assert expand_graph_support(g, by_id) == [g["content"], "harga 50000"]
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
