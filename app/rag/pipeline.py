"""Orkestrasi retrieval hybrid (D1): SATU implementasi, backend-agnostic.

Alur beku: embed → dense_top=50 + lexical_top=50 [+ graph] → RRF(k=60)
→ candidate_top=20 → truncasi batas kalimat (128/384/512) → rerank batch
→ final_top=5 (+ score_floor → no_evidence; rerank timeout → degraded).

v0.8: cabang graph opsional (graph=None = perilaku 2-way v0.7 identik).
Seed deterministik → BFS bounded → pseudo-chunk → RRF 3-way → floor ganda.

Murni: tanpa I/O selain via Port yang di-inject. Deterministik penuh untuk
given input (ties RRF dipecah deterministik) — contract test menegaskan ini.
"""

from __future__ import annotations

import re
from typing import Any

from app.rag.kg_seed import seed_candidates
from app.rag.normalize import normalize_id
from app.rag.ports import (
    BoundRetriever,
    CitedChunk,
    EmbeddingsPort,
    GraphStore,
    KgHit,
    RerankerPort,
    RetrievalResult,
    TokenizerPort,
    VectorStorePort,
)

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def rrf_fuse(
    dense: list[CitedChunk],
    lexical: list[CitedChunk],
    graph: list[CitedChunk] | None = None,
    *,
    k: int = 60,
    top_n: int = 20,
) -> list[CitedChunk]:
    """Fusi Reciprocal Rank Fusion: skor = Σ 1/(k+rank), rank 1-based.

    v0.8: cabang graph opsional (default kosong = perilaku 2-way v0.7).
    Deterministik: urut (-skor, rank_dense, rank_lexical, rank_graph, chunk_id).
    Chunk yang hanya muncul di 1 sisi tetap dihitung (1 suku).
    Skor final chunk = skor RRF (ditulis ulang ke field `score`).
    """
    graph = graph or []
    dense_rank = {c["chunk_id"]: i + 1 for i, c in enumerate(dense)}
    lex_rank = {c["chunk_id"]: i + 1 for i, c in enumerate(lexical)}
    graph_rank = {c["chunk_id"]: i + 1 for i, c in enumerate(graph)}
    by_id = {c["chunk_id"]: c
             for c in list(dense) + list(lexical) + list(graph)}
    scored: list[tuple[float, int, int, int, str]] = []
    for cid in by_id:
        s = 0.0
        dr = dense_rank.get(cid, 10**9)
        lr = lex_rank.get(cid, 10**9)
        gr = graph_rank.get(cid, 10**9)
        if cid in dense_rank:
            s += 1.0 / (k + dense_rank[cid])
        if cid in lex_rank:
            s += 1.0 / (k + lex_rank[cid])
        if cid in graph_rank:
            s += 1.0 / (k + graph_rank[cid])
        scored.append((s, dr, lr, gr, cid))
    scored.sort(key=lambda t: (-t[0], t[1], t[2], t[3], t[4]))
    out: list[CitedChunk] = []
    for s, _, _, _, cid in scored[:top_n]:
        c = dict(by_id[cid])
        c["score"] = s
        out.append(c)  # type: ignore[return-value]
    return out


def truncate_sentences(text: str, max_tokens: int, tok: TokenizerPort) -> str:
    """Potong ke max_tokens di BATAS KALIMAT (D5).

    Fallback: 1 kalimat pertama sudah > batas → potong per kata.
    Teks kosong / sudah muat → kembalikan apa adanya.
    """
    if not text or tok.count(text) <= max_tokens:
        return text
    sents = [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    if not sents:
        return tok.truncate_words(text, max_tokens)
    kept: list[str] = []
    for s in sents:
        trial = " ".join(kept + [s])
        if tok.count(trial) > max_tokens:
            break
        kept.append(s)
    if not kept:
        return tok.truncate_words(sents[0], max_tokens)
    return " ".join(kept)


class HybridRetriever:
    """Implementasi tunggal `BoundRetriever` (dibangun SUDAH terikat tenant).

    `store` yang di-inject sudah bound (factory) — pipeline tak pernah tahu tenant.
    """

    def __init__(
        self,
        *,
        embed: EmbeddingsPort,
        rerank: RerankerPort,
        store: VectorStorePort,
        tok: TokenizerPort,
        dense_top: int = 50,
        lexical_top: int = 50,
        rrf_k: int = 60,
        candidate_top: int = 20,
        final_top: int = 5,
        query_max_tok: int = 128,
        doc_max_tok: int = 384,
        pair_max_tok: int = 512,
        score_floor: float = 0.05,  # = default config (unit jujur, F-1)
        stem: bool = True,  # WAJIB sama dengan sisi index (RAG_STEMMER)
        # v0.8 graph (None = mati; perilaku 2-way v0.7 identik):
        graph: GraphStore | None = None,
        graph_seed_top: int = 8,
        graph_expand_top: int = 20,
        graph_max_hops: int = 2,
        graph_fanout: int = 50,
        graph_cap_agree: int = 2,  # dense+lex top-1 sepakat → cap kecil
        graph_cap_default: int = 6,  # tidak sepakat → cap besar
        graph_score_floor: float = 0.05,  # kalibrasi final F5
        graph_seed_confidence: float = 0.5,  # di bawah ini → graph-off
        graph_aliases: dict[str, str] | None = None,
    ) -> None:
        self._embed = embed
        self._rerank = rerank
        self._store = store
        self._tok = tok
        self._dense_top = dense_top
        self._lexical_top = lexical_top
        self._rrf_k = rrf_k
        self._candidate_top = candidate_top
        self._final_top = final_top
        self._query_max_tok = query_max_tok
        self._doc_max_tok = doc_max_tok
        self._pair_max_tok = pair_max_tok
        self._score_floor = score_floor
        self._stem = stem
        self._graph = graph
        self._graph_seed_top = graph_seed_top
        self._graph_expand_top = graph_expand_top
        self._graph_max_hops = graph_max_hops
        self._graph_fanout = graph_fanout
        self._graph_cap_agree = graph_cap_agree
        self._graph_cap_default = graph_cap_default
        self._graph_score_floor = graph_score_floor
        self._graph_seed_confidence = graph_seed_confidence
        self._graph_aliases = graph_aliases

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        import time as _time  # stdlib murni (arch-test OK, bukan asyncio)

        t0 = _time.perf_counter()
        timings: dict[str, float] = {}
        g_timings: dict[str, float] = {}
        g_counts: dict[str, int] = {}
        g_seed_conf: float | None = None
        g_degraded: list[str] = []

        def _trace(chunks: list, status: str, degraded: list,
                   ranked_n: int = 0) -> RetrievalResult:
            timings["total"] = round((_time.perf_counter() - t0) * 1000, 1)
            top = max((c["score"] for c in chunks), default=0.0)
            trace: dict[str, Any] = {
                "backend": getattr(self._store, "backend_name", "?"),
                "timings_ms": dict(timings),
                "counts": {"dense": len(dense), "lexical": len(lexical),
                           "candidates": len(candidates),
                           "ranked": ranked_n, "final": len(chunks)},
                "top_score": round(float(top), 4),
                "floor": self._score_floor,
                "status": status, "degraded": list(degraded),
                "query_preview": (query or "")[:120],
            }
            if self._graph is not None:
                trace["graph_timings_ms"] = dict(g_timings)
                trace["graph_counts"] = dict(g_counts)
                trace["seed_confidence"] = g_seed_conf
                trace["floors"] = {"natural": self._score_floor,
                                   "graph": self._graph_score_floor}
            return {
                "chunks": chunks, "status": status,  # type: ignore[typeddict-item]
                "degraded": list(degraded) + g_degraded,
                "trace": trace,  # type: ignore[typeddict-item]
            }

        # 1) Embed query (dipotong dulu: jangan buang konteks BGE-M3, P11).
        # Gagal = biarkan raise (D6: tanpa fallback beda-model).
        t = _time.perf_counter()
        qvec = (await self._embed.embed_texts(
            [truncate_sentences(query, self._query_max_tok, self._tok)]))[0]
        timings["embed"] = round((_time.perf_counter() - t) * 1000, 1)
        # 2) Fan-out ganda (F4: 50/50, bukan 20/20). SERIAL sengaja:
        # `asyncio.gather` butuh `import asyncio` = dilarang arch-test
        # (app/rag murni); biaya ~2x RTT store (±20–100ms PG) diterima.
        t = _time.perf_counter()
        dense = await self._store.search_dense(
            qvec, k=self._dense_top, filters=filters)
        timings["dense"] = round((_time.perf_counter() - t) * 1000, 1)
        t = _time.perf_counter()
        lexical = await self._store.search_lexical(
            normalize_id(query, stem=self._stem),
            k=self._lexical_top, filters=filters, raw_query=query)
        timings["lexical"] = round((_time.perf_counter() - t) * 1000, 1)
        # 3) v0.8 graph: seed → expand → pseudo-chunk (opsional; gagal →
        # graph_unavailable + lanjut vector, TIDAK PERNAH 500 — D11).
        graph_cuts: list[CitedChunk] = []
        if self._graph is not None:
            t = _time.perf_counter()
            cands = seed_candidates(
                query, self._graph_aliases)[:self._graph_seed_top]
            g_timings["seed"] = round((_time.perf_counter() - t) * 1000, 1)
            g_counts["seeds"] = len(cands)
            if cands:
                agree = (dense and lexical
                         and dense[0]["chunk_id"] == lexical[0]["chunk_id"])
                cap = (self._graph_cap_agree if agree
                       else self._graph_cap_default)
                g_counts["cap"] = cap
                # Strip filter non-access untuk cabang graph (keputusan
                # QA-F2/D13: graph hanya paham access_level).
                gf = ({"access_level": filters["access_level"]}
                      if filters and "access_level" in filters else None)
                t = _time.perf_counter()
                try:
                    hits = await self._graph.expand(
                        cands, k=self._graph_expand_top, filters=gf,
                        max_hops=1, fanout=self._graph_fanout)
                    hop1 = [h for h in hits if h["hops"] <= 1]
                    if (len(hop1) < 3 and self._graph_max_hops >= 2
                            and hits):
                        # Hop-2 hanya bila hop-1 kurus (hemat 1 RTT bila cukup).
                        more = await self._graph.expand(
                            cands, k=self._graph_expand_top, filters=gf,
                            max_hops=2, fanout=self._graph_fanout)
                        seen = {h["chunk_id"] for h in hits}
                        hits = hits + [h for h in more
                                       if h["chunk_id"] not in seen]
                except Exception:
                    g_timings["expand"] = round(
                        (_time.perf_counter() - t) * 1000, 1)
                    g_degraded.append("graph_unavailable")
                    hits = []
                else:
                    g_timings["expand"] = round(
                        (_time.perf_counter() - t) * 1000, 1)
                g_counts["paths"] = len(hits)
                # Confidence biner jujur: expand mengembalikan hit → seed kena
                # (1.0), else 0.0 = graph kosong normal, BUKAN degraded.
                g_seed_conf = 1.0 if hits else 0.0
                t = _time.perf_counter()
                if g_seed_conf >= self._graph_seed_confidence and hits:
                    backend = getattr(self._graph, "backend_name", "pggraph")
                    for h in sorted(hits, key=lambda x: -x["score"])[:cap]:
                        meta = dict(h.get("metadata") or {})
                        meta.setdefault("backend", backend)
                        graph_cuts.append({
                            "chunk_id": h["chunk_id"], "doc_id": h["doc_id"],
                            "content": h["content"], "source": h["source"],
                            "page": h.get("page"), "score": float(h["score"]),
                            "backend": backend, "metadata": meta,
                            "kind": "graph",
                            "provenance": list(
                                meta.get("provenance", [])),
                        })
                g_timings["verbalize"] = round(
                    (_time.perf_counter() - t) * 1000, 1)
                g_counts["kept"] = len(graph_cuts)
        # 4) RRF → kandidat (3-way bila graph aktif, else 2-way v0.7).
        candidates = rrf_fuse(dense, lexical, graph_cuts, k=self._rrf_k,
                              top_n=self._candidate_top)
        if not candidates:
            timings["rerank"] = 0.0
            return _trace([], "no_evidence", [])
        # 4) Truncasi rerank (D5): query + tiap dokumen di batas kalimat.
        q = truncate_sentences(query, self._query_max_tok, self._tok)
        docs = [truncate_sentences(c["content"], self._doc_budget(q), self._tok)
                for c in candidates]
        # 5) Rerank batch. Gagal APAPUN (timeout/koneksi/5xx/skema) →
        # degradasi eksplisit (RRF top-k + flag), BUKAN 500 (P0-2).
        # NOTE skala: skor final jalur ini = skor RRF (~0.016), BUKAN skor
        # rerank 0–1 — konsumen (citations/eval) wajib cek `degraded` (F-2).
        from app.core.errors import RerankError

        t = _time.perf_counter()
        try:
            ranked = await self._rerank.rerank(q, docs, top_n=len(docs))
        except (TimeoutError, RerankError):
            timings["rerank"] = round((_time.perf_counter() - t) * 1000, 1)
            final = candidates[: min(k, self._final_top)]
            return _trace(final, "ok", ["rerank_unavailable"])
        timings["rerank"] = round((_time.perf_counter() - t) * 1000, 1)
        # 6) Floor → no_evidence (D5): tolak tanpa LLM bila semua di bawah floor.
        # Urutan provider JANGAN dipercaya (sort sendiri); chunk di bawah
        # floor dibuang dari konteks final (anti-polusi sitasi, temuan F6).
        # Index di luar jangkauan = respons korup → buang (bukan IndexError).
        n = len(candidates)

        def _valid(r: dict) -> bool:
            if not (isinstance(r.get("index"), int)
                    and 0 <= r["index"] < n):
                return False
            s = r.get("score")
            # bool adalah subclass int — tolak eksplisit.
            return ((isinstance(s, float) or type(s) is int)
                    and s == s)  # NaN ditolak

        ranked = [r for r in ranked if _valid(r)]
        if not ranked:
            # Kandidat ada tapi respons rerank kosong/korup = bug provider —
            # raise (retryable) JANGAN no_evidence palsu.
            raise ValueError("respons rerank kosong/korup "
                             f"({n} kandidat dikirim)")
        ranked = sorted(ranked, key=lambda r: r["score"], reverse=True)
        # Floor ganda v0.8 (D12): natural vs verbal (distribusi skor beda).
        # graph_score_floor ≤ 0 = ikut floor natural (kalibrasi F5 final).
        gfloor = (self._graph_score_floor
                  if self._graph_score_floor > 0.0 else self._score_floor)

        def _floor_for(idx: int) -> float:
            c = candidates[idx]
            if c.get("kind") == "graph":
                return gfloor
            return self._score_floor

        if self._score_floor > 0.0 or gfloor > 0.0:
            passed = [r for r in ranked
                      if r["score"] >= _floor_for(r["index"])]
        else:
            passed = list(ranked)
        if not passed:
            return _trace([], "no_evidence", [], ranked_n=len(ranked))
        final: list[CitedChunk] = []
        for r in passed[: min(k, self._final_top)]:
            c = dict(candidates[r["index"]])
            c["score"] = float(r["score"])
            final.append(c)  # type: ignore[arg-type]
        return _trace(final, "ok", [], ranked_n=len(ranked))

    def _doc_budget(self, query: str) -> int:
        """Budget dokumen = doc_max, dikurangi query bila pair melebihi batas."""
        q_tok = self._tok.count(query)
        room = self._pair_max_tok - q_tok
        if room >= self._doc_max_tok:
            return self._doc_max_tok
        return max(32, room)
