"""Clearance ACL dokumen (v1.0 G6): satu-satunya definisi level akses.

Hierarki: public < internal < restricted. Clearance user = level MAKSIMUM
yang boleh dibaca; filter retrieval = semua level ⊆ clearance (fail-closed:
tak dikenal/kosong = public saja).
"""

from __future__ import annotations

LEVELS: tuple[str, ...] = ("public", "internal", "restricted")
DEFAULT_LEVEL = "public"


def normalize_level(value: object) -> str:
    """Kecilkan + validasi; tak dikenal → 'public' (fail-closed)."""
    v = str(value or "").strip().lower()
    return v if v in LEVELS else DEFAULT_LEVEL


def allowed_levels(clearance: object) -> list[str]:
    """Daftar level yang boleh dibaca pemilik clearance ini."""
    c = normalize_level(clearance)
    return list(LEVELS[:LEVELS.index(c) + 1])


def may_read(clearance: object, level: object) -> bool:
    """True bila clearance mencakup level dokumen."""
    return normalize_level(level) in allowed_levels(clearance)
