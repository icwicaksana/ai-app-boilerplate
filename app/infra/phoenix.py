"""Klien observability Phoenix (Arize). Infra = adaptor ke dunia luar.

Pola resmi docs (get-started-tracing): `phoenix.otel.register()` + auto-instrument.
- setup_phoenix_tracing(): dipanggil SEKALI di lifespan main.py. Daftarkan
  Phoenix sebagai tujuan trace + auto-instrumentasi semua paket OpenInference
  yang terinstall (LangChain, OpenAI SDK). No-op (False) jika
  PHOENIX_ENABLED=false atau paket belum diinstall — app tetap jalan.
- session_context(): bungkus eksekusi LangGraph agar thread_id tercatat
  sebagai session Phoenix (1 thread = 1 percakapan di UI).

Yang TIDAK di sini: prompt/eval/dataset (nanti, modul sendiri), thread_id
(dibuat controller), metadata bisnis (slot v1 via using_metadata).
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


def setup_phoenix_tracing(settings: Settings) -> bool:
    """Return True jika tracing aktif. Aman dipanggil tanpa paket terinstall."""
    if not settings.PHOENIX_ENABLED:
        log.info("phoenix tracing disabled (PHOENIX_ENABLED=false)")
        return False
    try:
        from phoenix.otel import register
    except ImportError as exc:  # pragma: no cover
        log.warning("phoenix tracing diminta tapi paket belum diinstall: %s "
                    "(pip install -r requirements.txt)", exc)
        return False

    try:
        register(
            project_name=settings.PHOENIX_PROJECT,
            endpoint=settings.PHOENIX_ENDPOINT,
            auto_instrument=True,
        )
    except TypeError:  # versi lama tanpa argumen endpoint — baca dari env/collector default
        log.warning("phoenix.otel.register() versi ini tanpa argumen endpoint; "
                    "pakai endpoint bawaan/env (PHOENIX_COLLECTOR_ENDPOINT)")
        register(project_name=settings.PHOENIX_PROJECT, auto_instrument=True)
    log.info("phoenix tracing enabled -> %s (project=%s)",
             settings.PHOENIX_ENDPOINT, settings.PHOENIX_PROJECT)
    return True


def session_context(thread_id: str) -> Any:
    """Context manager: catat span dalam 1 session Phoenix = thread_id kita.

    Tanpa instrumentor terinstall → nullcontext (no-op, app tetap jalan).
    """
    try:
        from openinference.instrumentation import using_session
        return using_session(thread_id)
    except ImportError:  # pragma: no cover
        return nullcontext()
