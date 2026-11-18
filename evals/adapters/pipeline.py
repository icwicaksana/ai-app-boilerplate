"""Adapter eval pipeline retrieval (V1-E): HybridRetriever + fake port.

Mekanika deterministik: rank, floor, degradasi, intersect filter.
Kualitas retrieval live diukur gold30/gold-graph (tier nightly).
"""

from __future__ import annotations

from typing import Any


class _Tok:
    def count(self, t):
        return max(1, len((t or "").split()))

    def truncate_words(self, t, n):
        return " ".join((t or "").split()[:n])


class _Embed:
    async def embed_texts(self, texts):
        return [[0.1] for _ in texts]


class _Rerank:
    def __init__(self, spec):
        self._spec = spec or {}

    async def rerank(self, q, docs, top_n=None):
        if self._spec.get("timeout"):
            raise TimeoutError("skenario")
        if self._spec.get("corrupt"):
            return [{"index": 9999, "score": 0.99}]
        scores = self._spec.get("scores")
        if scores is None:
            scores = [1.0 - i * 0.1 for i in range(len(docs))]
        order = sorted(range(len(docs)), key=lambda i: -scores[i])
        return [{"index": i, "score": scores[i]}
                for i in order[:top_n]]


class _Store:
    backend_name = "fake"

    def __init__(self, dense, lex):
        self._dense, self._lex = dense, lex
        self.seen: list = []

    async def search_dense(self, qv, k=50, filters=None):
        self.seen.append(("dense", filters))
        return list(self._dense)

    async def search_lexical(self, q, k=50, filters=None, raw_query=""):
        self.seen.append(("lex", filters))
        return list(self._lex)


def _chunk(spec: dict, i: int) -> dict:
    return {"chunk_id": spec.get("cid", f"c{i}"),
            "doc_id": "d", "content": spec.get("content", "isi"),
            "source": "s.md", "page": None, "score": 0.0,
            "backend": "fake", "metadata": {}}


async def run(case: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    from app.rag.pipeline import HybridRetriever

    spec = case.get("input") or {}
    dense = [_chunk(s, i) for i, s in enumerate(spec.get("dense", []))]
    lex = [_chunk(s, i) for i, s in enumerate(spec.get("lex", []))]
    store = _Store(dense, lex)
    r = HybridRetriever(
        embed=_Embed(), rerank=_Rerank(spec.get("rerank")),
        store=store, tok=_Tok(),
        score_floor=float(spec.get("floor", 0.05)),
        default_filters=spec.get("default_filters"))
    res = await r.search(spec.get("query", "q?"),
                         k=int(spec.get("k", 5)),
                         filters=spec.get("filters"))
    return {"status": res["status"],
            "chunk_ids": [c["chunk_id"] for c in res["chunks"]],
            "grounded": res.get("grounded", True),
            "degraded": res.get("degraded") or [],
            "store_filters": [dict(f or {}) for _, f in store.seen]}
