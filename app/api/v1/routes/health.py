"""Health: liveness + kesiapan DB/LLM. Tidak pernah 500 — laporkan degraded."""

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.infra.postgres import is_postgres_configured

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    provider = settings.MODEL_PROVIDER.lower()
    if provider in ("opencode_go", "opencode"):
        llm_configured = bool(settings.OPENCODE_API_KEY)
        llm_hint = "missing OPENCODE_API_KEY"
        model = settings.OPENCODE_MODEL
    else:
        llm_configured = bool(settings.OPENAI_API_KEY)
        llm_hint = "missing OPENAI_API_KEY"
        model = settings.MODEL_NAME
    return {
        "status": "ok" if llm_configured else "degraded",
        "service": settings.PROJECT_NAME,
        "version": "v0",
        "checks": {
            "llm": f"{provider}:{model}" if llm_configured else llm_hint,
            "checkpointer": "postgres" if is_postgres_configured(settings.DATABASE_URL) else "memory",
            "hitl": "enabled" if settings.HITL_ENABLED else "disabled (v0)",
        },
    }


@router.get("/ready")
async def ready(settings: Settings = Depends(get_settings)) -> dict:
    return await health(settings)
