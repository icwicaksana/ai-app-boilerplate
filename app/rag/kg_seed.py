"""Kandidat seed entity dari query (murni, deterministik, tanpa model).

Urutan: frasa berquote → span kapital → token panjang → full query (dibatasi).
Alias tenant diterapkan di ADAPTER (`resolve_seeds`, tabel kg_aliases);
param `aliases` di sini = override eval/fixture (bukan jalur produksi).
"""

from __future__ import annotations

import re

from app.rag.normalize import normalize_entity

_QUOTE_RE = re.compile(r'"([^"]{2,80})"')
_CAP_RE = re.compile(r"\b[A-ZÀ-Ž][\w'.-]*(?:\s+[A-ZÀ-Ž][\w'.-]*){0,4}")
_TOKEN_RE = re.compile(r"[a-zà-ž0-9][a-zà-ž0-9'.-]{3,31}")
_MAX_FULLQUERY_WORDS = 8  # full-query fallback: tolak kalimat panjang (noise)


def seed_candidates(query: str,
                    aliases: dict[str, str] | None = None) -> list[str]:
    """Ekstrak norm kandidat seed, dedup, urut prioritas. Tak pernah raise."""
    out: list[str] = []
    seen: set[str] = set()

    def _push(raw: str) -> None:
        norm = normalize_entity(raw)
        if not norm or norm in seen:
            return
        if aliases and norm in aliases:
            norm = aliases[norm]
            if norm in seen:
                return
        seen.add(norm)
        out.append(norm)

    for m in _QUOTE_RE.finditer(query or ""):
        _push(m.group(1))
    for m in _CAP_RE.finditer(query or ""):
        if len(m.group(0).strip()) >= 3:
            _push(m.group(0))
    # Token-level (lowercase Indonesia: "komputer jinjing" → 2 kandidat).
    for tok in _TOKEN_RE.findall((query or "").lower()):
        _push(tok)
    full = (query or "").strip()
    if full and len(full.split()) <= _MAX_FULLQUERY_WORDS:
        _push(full)
    return out
