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

# True HANYA bila setup_phoenix_tracing() sukses memasang exporter.
# Dibaca per-request oleh session_context(); ditulis sekali saat lifespan.
_TRACING_ACTIVE = False


def is_tracing_active() -> bool:
    """Status tracing aktual (bedakan dari flag config mentah).

    PHOENIX_ENABLED=true tapi collector mati / register gagal = tetap False.
    """
    return _TRACING_ACTIVE


def setup_phoenix_tracing(settings: Settings) -> bool:
    """Return True jika tracing aktif. Aman dipanggil tanpa paket terinstall."""
    global _TRACING_ACTIVE
    _TRACING_ACTIVE = False  # reset dulu: semua early-return = tidak aktif.
    if not settings.PHOENIX_ENABLED:
        log.info("phoenix tracing disabled (PHOENIX_ENABLED=false)")
        return False
    # Cek keterjangkauan SEBELUM memasang exporter: OTLP exporter melakukan
    # retry+backoff saat collector mati → SETIAP request jadi lambat (puluhan
    # detik, terbukti saat uji integrasi). Lebih baik matikan dengan warning.
    if not _endpoint_reachable(settings.PHOENIX_ENDPOINT):
        log.warning("PHOENIX_ENABLED=true tapi collector tak terjangkau (%s) — "
                    "tracing DIMATIKAN agar request tidak lambat. Nyalakan "
                    "`docker compose up -d phoenix` di Infra/ atau set "
                    "PHOENIX_ENABLED=false.", settings.PHOENIX_ENDPOINT)
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
    _TRACING_ACTIVE = True
    return True


def _endpoint_reachable(endpoint: str) -> bool:
    """GET cepat ke base collector; False bila tak terjangkau (timeout 1 dtk)."""
    try:
        import httpx

        base = (endpoint or "").split("/v1/")[0].rstrip("/")
        if not base:
            return False
        return httpx.get(base, timeout=1.0).status_code < 500
    except Exception:
        return False


def session_context(thread_id: str) -> Any:
    """Context manager: catat span dalam 1 session Phoenix = thread_id kita.

    No-op (nullcontext) bila tracing TAK AKTIF — tanpa attach OTel sama
    sekali. Dulu hanya ImportError yang jadi no-op: paket terinstall +
    tracing mati = attach sia-sia yang meledak saat detach dari context
    berbeda (ERROR "Failed to detach context" di tiap chat/stream selesai).
    """
    if not _TRACING_ACTIVE:
        return nullcontext()
    try:
        from openinference.instrumentation import using_session
        return using_session(thread_id)
    except ImportError:  # pragma: no cover
        return nullcontext()
