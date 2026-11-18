"""Kolektor trace eksekusi per-turn (WS3, kontrak: docs/TRACE-CONTRACT.md).

Disiplin contextvar = tiru `_current_failures` (DelegationTool): `runner.stream`
memasang list segar (`start_trace`) dan melepasnya (`drain_trace`/
`discard_trace`) — `set/baca/reset` HANYA di task produsen. Tool
(delegasi/skill/runner-timing) hanya `record_span` (append best-effort yang
tak pernah raise); di luar trace aktif = no-op (jobs sync TANPA trace).

Sanitasi via helper existing `neutralize_delimiters` (JANGAN regex baru).
Redaksi secret via helper existing `looks_like_secret` + denylist key
(JANGAN regex baru) — TERPUSAT di `summarize_input` sehingga berlaku untuk
`input_summary` tool maupun `Brief.task` delegasi.
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from typing import Any, Literal

from app.core.logging import get_logger
from app.core.untrusted import neutralize_delimiters
from app.infra.memory import looks_like_secret

log = get_logger(__name__)

TRACE_VERSION = 1
MAX_SPANS = 20
MAX_INPUT_SUMMARY_CHARS = 500
MAX_DETAIL_CHARS = 500

#: Pengganti nilai yang diredaksi (P0: stabil, tanpa sisa secret).
REDACTED = "[redacted]"

SpanKind = Literal["tool", "subagent", "skill"]
SpanStatus = Literal["ok", "partial", "failed", "timeout", "skipped", "pending"]

__all__ = [
    "MAX_DETAIL_CHARS",
    "MAX_INPUT_SUMMARY_CHARS",
    "MAX_SPANS",
    "REDACTED",
    "TRACE_VERSION",
    "discard_trace",
    "drain_trace",
    "record_span",
    "sanitize_detail",
    "start_trace",
    "summarize_input",
    "trace_active",
]

_current_spans: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "run_trace_spans", default=None)

#: Denylist key input yang nilainya SELALU diredaksi (P0, tanpa regex baru:
#: cocok exact case-insensitive setelah normalisasi `_`). Berlaku untuk
#: `input_summary` tool + `Brief.task`/constraints delegasi via
#: `summarize_input` (satu-satunya pintu ringkasan trace).
_REDACT_KEYS = frozenset({
    "password", "sandi", "otp", "pin", "nik", "ktp", "api_key", "token",
})

_VALID_KINDS = frozenset({"tool", "subagent", "skill"})
_VALID_STATUSES = frozenset(
    {"ok", "partial", "failed", "timeout", "skipped", "pending"})


def trace_active() -> bool:
    """True bila ada trace yang sedang dikumpulkan di task ini."""
    return _current_spans.get() is not None


def start_trace() -> Token:
    """Pasang kolektor segar. HANYA dipanggil task produsen (runner.stream)."""
    return _current_spans.set([])


def _is_redact_key(key: Any) -> bool:
    """True bila nama key masuk denylist (cocok string, tanpa regex baru)."""
    norm = str(key or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not norm:
        return False
    if norm in _REDACT_KEYS:
        return True
    # Varian umum tanpa regex: access_token, api-key, kata sandi, passwd…
    # ("sandi"/"password"/"token" aman sebagai substring; "otp"/"pin"/"nik"/
    # "ktp" pendek → exact saja agar "opini"/"unik" tak ikut kena.)
    return ("password" in norm or "passwd" in norm or "sandi" in norm
            or norm == "pwd" or "token" in norm or norm.endswith("_key")
            or ("api" in norm and "key" in norm))


def _redact_value(value: Any) -> Any:
    """Redaksi per-nilai: whole-node jadi `[redacted]` bila tampak secret."""
    if value is None:
        return None
    if isinstance(value, str):
        return REDACTED if looks_like_secret(value) else value
    if isinstance(value, (bool, int, float)):
        # NIK/kartu sebagai angka mentah tetap tertangkap jaring digit-10+;
        # angka kecil/boolean lolos (bukan secret).
        try:
            probe = str(value)
        except Exception:
            return REDACTED
        return REDACTED if looks_like_secret(probe) else value
    # Node bersarang (dict/list): redaksi whole-node fail-closed bila
    # serialisasinya tampak secret (termasuk key denylist di dalamnya).
    try:
        probe = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        try:
            probe = str(value)
        except Exception:
            return REDACTED
    return REDACTED if looks_like_secret(probe) else value


def summarize_input(value: Any) -> str:
    """Ringkasan input 1 baris <= 500 char + REDAKSI terpusat (P0).

    - `dict`: nilai per key denylist → `[redacted]`; nilai lain diredaksi
      bila `looks_like_secret` (helper existing, tanpa regex baru).
    - `str`/lainnya: seluruh teks → `[redacted]` bila tampak secret
      (mencakup `Brief.task` delegasi yang berupa teks bebas).
    """
    if isinstance(value, dict):
        redacted = {k: (REDACTED if _is_redact_key(k) else _redact_value(v))
                    for k, v in value.items()}
        try:
            text = json.dumps(redacted, sort_keys=True, ensure_ascii=False,
                              default=str)
        except (TypeError, ValueError):
            text = str(redacted)
        # JANGAN cek whole-text di sini: nama key denylist ("password")
        # memang memicu heuristik — nilainya sudah diredaksi per-key.
        return neutralize_delimiters(text, limit=MAX_INPUT_SUMMARY_CHARS)
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    if text and looks_like_secret(text):
        return REDACTED
    return neutralize_delimiters(text, limit=MAX_INPUT_SUMMARY_CHARS)


def sanitize_detail(text: str) -> str:
    """Detail span 1 baris <= 500 char (helper existing, tanpa regex baru)."""
    return neutralize_delimiters(text or "", limit=MAX_DETAIL_CHARS)


def record_span(*, tool: str, kind: SpanKind, status: SpanStatus,
                ms: int = 0, attempts: int = 1,
                input_summary: str = "", detail: str = "") -> None:
    """Catat 1 span ke trace berjalan. Tak pernah raise; tanpa trace = no-op.

    `kind`/`status` tak dikenal di-coerce ke default aman + warning (P1)
    agar skema beku tak pernah dilanggar pemanggil yang typo.
    """
    try:
        spans = _current_spans.get()
        if spans is None:
            return
        kind_s, status_s = str(kind), str(status)
        if kind_s not in _VALID_KINDS:
            log.warning("run_trace kind tak dikenal %r → 'tool'", kind_s)
            kind_s = "tool"
        if status_s not in _VALID_STATUSES:
            log.warning("run_trace status tak dikenal %r → 'failed'",
                        status_s)
            status_s = "failed"
        spans.append({
            "tool": str(tool or "tool"),
            "kind": kind_s,
            "status": status_s,
            "ms": max(0, int(ms)),
            "attempts": max(1, int(attempts)),
            "input_summary": str(input_summary or ""),
            "detail": str(detail or ""),
        })
    except Exception:
        pass  # best-effort: telemetri tak boleh mematahkan tool


def drain_trace(token: Token, *, complete: bool,
                pending_approvals: int | None = None) -> dict[str, Any]:
    """Ambil trace + lepas kolektor. Cap 20 spans, sisanya = truncated=True."""
    try:
        spans = _current_spans.get() or []
    finally:
        try:
            _current_spans.reset(token)
        except Exception as exc:
            log.debug("run_trace drain reset gagal: %s",
                      type(exc).__name__)
    truncated = len(spans) > MAX_SPANS
    trace: dict[str, Any] = {
        "trace_version": TRACE_VERSION,
        "complete": bool(complete),
        "spans": spans[:MAX_SPANS],
        "truncated": truncated,
    }
    if not complete and pending_approvals is not None:
        trace["pending_approvals"] = max(0, int(pending_approvals))
    return trace


def discard_trace(token: Token) -> None:
    """Lepas kolektor tanpa membangun trace (jalur error/abort)."""
    try:
        _current_spans.reset(token)
    except Exception as exc:
        log.debug("run_trace discard reset gagal: %s",
                  type(exc).__name__)
