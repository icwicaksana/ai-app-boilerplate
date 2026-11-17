"""Alarm degradasi terpusat (v1.0 S9): fail-open TIDAK BOLEH diam-diam.

Setiap titik fail-open (denylist, rate-limit, idempotency, revoke, ws_auth,
turns) WAJIB memanggil `report_degraded()` — bukan sekadar log warning.
Tiga keluaran:
1. Log ERROR bermarkah stabil `ALARM degraded ...` (alert berbasis log,
   bisa dipakai HARI INI tanpa infra tambahan).
2. Counter in-memory per komponen (`degraded_counts()`) — jahitan untuk
   `/metrics` Prometheus di V1-F (registry diganti collector di sana).
3. Flag `request.state.degraded` (bila request tersedia) → header respons
   `X-Degraded` oleh middleware (klien tahu kapan harus ekstra hati-hati,
   mis. retry idempoten sendiri saat dedup mati).
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

_counters: dict[str, int] = {}


def report_degraded(component: str, reason: str, *,
                    request: Any | None = None) -> None:
    """Catat 1 kejadian fail-open. Tak pernah raise (alarm tak boleh rusuh)."""
    try:
        _counters[component] = _counters.get(component, 0) + 1
        total = _counters[component]
    except Exception:
        total = -1
    try:
        log.error("ALARM degraded component=%s reason=%s total=%d",
                  component, reason, total)
    except Exception:
        pass
    if request is not None:
        flag_request(request, component)


def flag_request(request: Any, component: str) -> None:
    """Tandai request terdegradasi (tanpa alarm baru — untuk keputusan guard
    yang sudah menghitung sendiri, mis. IdemDecision.degraded)."""
    try:
        degraded = getattr(request.state, "degraded", None)
        if degraded is None:
            request.state.degraded = degraded = set()
        degraded.add(component)
    except Exception:
        pass


def degraded_counts() -> dict[str, int]:
    """Salinan counter (jahitan /metrics V1-F; asersi tes chaos)."""
    return dict(_counters)


def reset_degraded_counts() -> None:
    """Nolkan counter (HANYA tes)."""
    _counters.clear()
