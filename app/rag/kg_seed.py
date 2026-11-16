"""Kandidat seed entity dari query (murni, deterministik, tanpa model).

Urutan: frasa berquote → span kapital → full query. Alias tenant
(norm → norm kanonis) diterapkan terakhir. Cap di pipeline (seed_top).
"""

from __future__ import annotations

import re

from app.rag.normalize import normalize_entity

_QUOTE_RE = re.compile(r'"([^"]{2,80})"')
_CAP_RE = re.compile(r"\b[A-ZÀ-Ž][\w'.-]*(?:\s+[A-ZÀ-Ž][\w'.-]*){0,4}")


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
    _push(query or "")
    return out
