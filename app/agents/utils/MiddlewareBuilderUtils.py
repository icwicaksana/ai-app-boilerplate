"""Susun onion middleware agent. v0: HITL OFF -> []. v1: tambah HITL jika enabled."""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


def build_middleware(settings: Settings) -> list:
    if not getattr(settings, "HITL_ENABLED", False):
        return []
    try:
        from langchain.agents.middleware import HumanInTheLoopMiddleware  # type: ignore

        from app.agents.middleware.HumanInTheLoopMiddleware import INTERRUPT_ON

        if INTERRUPT_ON:
            return [HumanInTheLoopMiddleware(interrupt_on=dict.fromkeys(INTERRUPT_ON, True))]
        log.warning("HITL_ENABLED=true tapi INTERRUPT_ON kosong — middleware nonaktif")
    except ImportError as exc:
        log.warning("HITL diminta tapi paket middleware belum tersedia: %s", exc)
    return []
