"""Helper bersama konektor: identifier aman + watermark + render baris (murni)."""

from __future__ import annotations

import re
from datetime import datetime

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Kata tulis DILARANG muncul di query konektor (lapis 2, M4).
WRITE_DENYLIST = ("insert", "update", "delete", "drop", "alter", "create",
                  "truncate", "grant", "revoke", "copy", "merge", "replace")


def check_ident(name: str, *, kind: str = "identifier") -> str:
    """Validasi nama tabel/kolom/skema. Raise bila bukan identifier polos."""
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"{kind} tak valid: {name!r}")
    return name


def check_readonly_sql(sql: str) -> str:
    """Tolak query mengandung kata tulis (case-insensitive, word-boundary)."""
    lowered = f" {sql.lower()} "
    for word in WRITE_DENYLIST:
        if f" {word} " in lowered or f" {word}(" in lowered:
            raise ValueError(f"query konektor mengandung kata tulis: {word}")
    return sql


def parse_watermark(watermark: str) -> tuple[str, str]:
    """'2026-01-01T00:00:00+00:00|123' → (ts, id). Kosong = awal zaman."""
    if not watermark:
        return "1970-01-01T00:00:00+00:00", ""
    if "|" not in watermark:
        raise ValueError(f"watermark tak valid: {watermark!r}")
    ts, _, pk = watermark.partition("|")
    datetime.fromisoformat(ts)  # validasi (raise bila sampah)
    return ts, pk


def format_watermark(ts: object, pk: object) -> str:
    ts_s = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    return f"{ts_s}|{pk}"


def render_row(table: str, row: dict, *, pk_cols: list[str]) -> str:
    """Baris → 1 kalimat faktual ('tabel | k: v | ...', tanpa null)."""
    cells = " | ".join(f"{k}: {v}" for k, v in row.items()
                       if v is not None and str(v).strip())
    return f"[{table}] {cells}".strip()
