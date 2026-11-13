"""Orkestrasi retrieval hybrid (D1): SATU implementasi, backend-agnostic.

Alur beku: embed → dense_top=50 + lexical_top=50 → RRF(k=60) → candidate_top=20
→ truncasi batas kalimat (128/384/512) → rerank batch → final_top=5
(+ score_floor → no_evidence; rerank timeout → degraded).

Murni: tanpa I/O selain via Port yang di-inject. Deterministik penuh untuk
given input (ties RRF dipecah deterministik) — contract test menegaskan ini.
"""

from __future__ import annotations

import re
from typing import Any

from app.rag.ports import (
    BoundRetriever,
    CitedChunk,
    EmbeddingsPort,
    RerankerPort,
    RetrievalResult,
    TokenizerPort,
    VectorStorePort,
)

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def rrf_fuse(
    dense: list[CitedChunk],
    lexical: list[CitedChunk],
    *,
    k: int = 60,
    top_n: int = 20,
) -> list[CitedChunk]:
    """Fusi Reciprocal Rank Fusion: skor = Σ 1/(k+rank), rank 1-based.

    Deterministik: urut (-skor, rank_dense, rank_lexical, chunk_id).
    Chunk yang hanya muncul di 1 sisi tetap dihitung (1 suku).
    Skor final chunk = skor RRF (ditulis ulang ke field `score`).
    """
    dense_rank = {c["chunk_id"]: i + 1 for i, c in enumerate(dense)}
    lex_rank = {c["chunk_id"]: i + 1 for i, c in enumerate(lexical)}
    by_id = {c["chunk_id"]: c for c in list(dense) + list(lexical)}
    scored: list[tuple[float, int, int, str]] = []
    for cid in by_id:
        s = 0.0
        dr = dense_rank.get(cid, 10**9)
        lr = lex_rank.get(cid, 10**9)
        if cid in dense_rank:
            s += 1.0 / (k + dense_rank[cid])
        if cid in lex_rank:
            s += 1.0 / (k + lex_rank[cid])
        scored.append((s, dr, lr, cid))
    scored.sort(key=lambda t: (-t[0], t[1], t[2], t[3]))
    out: list[CitedChunk] = []
    for s, _, _, cid in scored[:top_n]:
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

        def _trace(chunks: list, status: str, degraded: list,
                   ranked_n: int = 0) -> RetrievalResult:
            timings["total"] = round((_time.perf_counter() - t0) * 1000, 1)
            top = max((c["score"] for c in chunks), default=0.0)
            return {
                "chunks": chunks, "status": status, "degraded": degraded,  # type: ignore[typeddict-item]
                "trace": {
                    "backend": getattr(self._store, "backend_name", "?"),
                    "timings_ms": dict(timings),
                    "counts": {"dense": len(dense), "lexical": len(lexical),
                               "candidates": len(candidates),
                               "ranked": ranked_n, "final": len(chunks)},
                    "top_score": round(float(top), 4),
                    "floor": self._score_floor,
                    "status": status, "degraded": list(degraded),
                    "query_preview": (query or "")[:120],
                },
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
            query, k=self._lexical_top, filters=filters)
        timings["lexical"] = round((_time.perf_counter() - t) * 1000, 1)
        # 3) RRF → kandidat.
        candidates = rrf_fuse(dense, lexical, k=self._rrf_k,
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
        if self._score_floor > 0.0:
            passed = [r for r in ranked if r["score"] >= self._score_floor]
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
