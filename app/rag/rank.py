"""Ranking leksikal client-side (v1.0 G7): BM25-lite untuk Qdrant.

Latar: `MatchText` Qdrant = boolean (cocok/tidak) tanpa skor bermakna;
cabang lexical PG memakai ts_rank. Tanpa ranking, urutan kandidat Qdrant
(doc_id, ord) merusak RRF hybrid → recall timpang antar-backend.

BM25-lite: IDF query-lokal (df dari kandidat terambil, bukan korpus —
aproksimasi jujur: ordinal benar untuk RRF, skala tak sebanding ts_rank),
panjang dinormalisasi avgdl kandidat. Deterministik (tiebreak stabil).
HANYA ordinal yang dipakai pipeline (rrf_fuse = rank-based).
"""

from __future__ import annotations

import math

K1 = 1.2
B = 0.75


def rank_bm25_lite(query_terms: list[str],
                   docs: list[dict]) -> list[tuple[int, float]]:
    """Skor (idx, skor) terurut menurun. `docs[i]` butuh kunci "tokens".

    Token = sudah dinormalisasi pemanggil (lower/stem konsisten index).
    Dokumen tanpa token bersama → skor 0.0 (tetap diurut stabil di akhir).
    """
    terms = [t for t in query_terms if t]
    if not terms or not docs:
        return [(i, 0.0) for i in range(len(docs))]
    tok_lists = [list(d.get("tokens") or []) for d in docs]
    lengths = [len(t) or 1 for t in tok_lists]
    avgdl = sum(lengths) / len(lengths)
    n = len(docs)
    # df query-lokal: di berapa kandidat term muncul.
    df: dict[str, int] = {}
    for t in set(terms):
        df[t] = sum(1 for toks in tok_lists if t in toks)
    scored: list[tuple[int, float]] = []
    for i, toks in enumerate(tok_lists):
        score = 0.0
        for t in terms:
            tf = toks.count(t)
            if not tf:
                continue
            # IDF Robertson–Spärck Jones dengan smoothing + lantai 0.
            idf = max(0.0, math.log((n - df[t] + 0.5) / (df[t] + 0.5) + 1.0))
            denom = tf + K1 * (1.0 - B + B * lengths[i] / avgdl)
            score += idf * (tf * (K1 + 1.0)) / denom
        scored.append((i, round(score, 6)))
    # Skor menurun; tiebreak = idx (stabil, deterministik).
    scored.sort(key=lambda p: (-p[1], p[0]))
    return scored
