"""Kandidat seed entity dari query (murni, deterministik, tanpa model).

Urutan prioritas: frasa berquote → span kapital → n-gram lowercase (3,2) →
token → full query (dibatasi). Bobot tier untuk `seed_confidence` =
bobot-tertinggi yang MATCH entity (bukan rasio match/request — itu kolaps
saat recall-net token besar; pelajaran F5 run 1: gused=0 di semua sel).

Alias workspace diterapkan di ADAPTER (`resolve_seeds`, tabel kg_aliases);
param `aliases` di sini = override eval/fixture (bukan jalur produksi).
"""

from __future__ import annotations

import re

from app.rag.normalize import normalize_entity

_QUOTE_RE = re.compile(r'"([^"]{2,80})"')
_CAP_RE = re.compile(r"\b[A-ZÀ-Ž][\w'.-]*(?:\s+[A-ZÀ-Ž][\w'.-]*){0,4}")
_TOKEN_RE = re.compile(r"[a-zà-ž0-9][a-zà-ž0-9'.-]{2,31}")
_WORD_RE = re.compile(r"[a-zà-ž0-9][a-zà-ž0-9'.-]*")
_MAX_FULLQUERY_WORDS = 8  # full-query fallback: tolak kalimat panjang (noise)

W_QUOTE = 1.0
W_CAPS = 0.9
W_NGRAM = 0.8
W_FULLQUERY = 0.7
W_TOKEN = 0.6


def preseed_aliases(query: str, aliases: dict[str, str]) -> list[str]:
    """Canonical dari alias yang muncul verbatim di query (longest-first).

    Menjamin alias multi-kata terjangkau tanpa tergantung cutoff seed_top.
    """
    qn = normalize_entity(query or "")
    out: list[str] = []
    for alias in sorted(aliases or {}, key=len, reverse=True):
        if alias and alias in qn:
            canon = aliases[alias]
            if canon not in out:
                out.append(canon)
    return out


def seed_candidates_weighted(
        query: str, aliases: dict[str, str] | None = None,
) -> list[tuple[str, float]]:
    """(norm, bobot-tier), dedup (bobot tertinggi menang), urut prioritas."""
    best: dict[str, float] = {}

    def _push(raw: str, w: float) -> None:
        norm = normalize_entity(raw)
        if not norm:
            return
        if aliases and norm in aliases:
            norm = aliases[norm]
        if w > best.get(norm, 0.0):
            best[norm] = w

    for m in _QUOTE_RE.finditer(query or ""):
        _push(m.group(1), W_QUOTE)
    for m in _CAP_RE.finditer(query or ""):
        if len(m.group(0).strip()) >= 3:
            _push(m.group(0), W_CAPS)
    words = [w for w in _WORD_RE.findall((query or "").lower())
             if len(w) >= 2]
    for n in (3, 2):
        for i in range(len(words) - n + 1):
            _push(" ".join(words[i:i + n]), W_NGRAM)
    for tok in _TOKEN_RE.findall((query or "").lower()):
        _push(tok, W_TOKEN)
    full = (query or "").strip()
    if full and len(full.split()) <= _MAX_FULLQUERY_WORDS:
        _push(full, W_FULLQUERY)
    order = {W_QUOTE: 0, W_CAPS: 1, W_NGRAM: 2, W_TOKEN: 3, W_FULLQUERY: 4}
    return sorted(best.items(), key=lambda kv: (order[kv[1]], kv[0]))


def seed_candidates(query: str,
                    aliases: dict[str, str] | None = None) -> list[str]:
    """Ekstrak norm kandidat seed, dedup, urut prioritas. Tak pernah raise."""
    return [n for n, _ in seed_candidates_weighted(query, aliases)]
