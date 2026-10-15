"""Health: liveness + kesiapan DB/LLM/gateway. Tidak pernah 500 — laporkan degraded."""

import httpx
from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.infra.postgres import is_postgres_configured

router = APIRouter(tags=["health"])

APP_VERSION = "v0.2"
_GATEWAY_TIMEOUT_SECONDS = 2.0  # health tak boleh lambat karena gateway


async def _gateway_status(settings: Settings) -> str:
    """Cek LiteLLM proxy: ok / down / unconfigured. Tak pernah raise."""
    if not settings.LITELLM_API_KEY or not settings.LITELLM_MODEL:
        return "unconfigured (isi LITELLM_API_KEY + LITELLM_MODEL)"
    url = settings.LITELLM_BASE_URL.rstrip("/") + "/health/liveliness"
    try:
        async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
        return "ok" if resp.status_code < 500 else f"down (http {resp.status_code})"
    except Exception as exc:
        return f"down ({type(exc).__name__})"


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    gateway = await _gateway_status(settings)
    llm_configured = gateway == "ok"
    return {
        "status": "ok" if llm_configured else "degraded",
        "service": settings.PROJECT_NAME,
        "version": APP_VERSION,
        "checks": {
            "llm": f"litellm:{settings.LITELLM_MODEL or '-'}" if settings.LITELLM_MODEL else "missing LITELLM_MODEL",
            "gateway": gateway,
            "checkpointer": "postgres" if is_postgres_configured(settings.DATABASE_URL) else "memory",
            "hitl": "enabled" if settings.HITL_ENABLED else "disabled",
        },
    }


@router.get("/ready")
async def ready(settings: Settings = Depends(get_settings)) -> dict:
    return await health(settings)
