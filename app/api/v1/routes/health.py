"""Health: liveness + kesiapan DB/LLM/gateway. Tidak pernah 500 — laporkan degraded."""

import asyncio

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])

_GATEWAY_TIMEOUT_SECONDS = 2.0  # health tak boleh lambat karena gateway
_DB_PROBE_TIMEOUT_SECONDS = 2.0  # idem karena database


async def _gateway_status(settings: Settings) -> str:
    """Cek LiteLLM proxy: ok / down / unconfigured. Tak pernah raise."""
    if not settings.LITELLM_API_KEY.strip() or not settings.LITELLM_MODEL.strip():
        return "unconfigured (isi LITELLM_API_KEY + LITELLM_MODEL)"
    url = settings.LITELLM_BASE_URL.rstrip("/") + "/health/liveliness"
    try:
        async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
        # 401/403 (key salah) = DOWN, bukan ok — hanya 2xx yang sehat.
        return "ok" if 200 <= resp.status_code < 300 else f"down (http {resp.status_code})"
    except Exception as exc:
        return f"down ({type(exc).__name__})"


async def _persistence_status(request: Request, settings: Settings) -> tuple[str, str]:
    """Pool + store dari lifespan + probe live `SELECT 1`. Tak pernah raise.

    Keberadaan objek pool TIDAK cukup (DB bisa mati setelah startup) —
    sehingga health selalu uji 1 query cepat bertimeout. Akuisisi koneksi
    ikut dibatasi timeout: pool exhausted = kondisi yang justru ingin
    dilaporkan, bukan digantung (temuan F-05).
    """
    state = request.app.state
    pool = getattr(state, "pool", None)
    store = getattr(state, "store", None)
    if pool is None:
        if not (settings.DATABASE_URL or "").strip():
            problem = "missing DATABASE_URL (wajib sejak v0.3)"
        else:
            problem = "not initialized (lifespan belum jalan/gagal?)"
        return problem, problem
    try:
        async with asyncio.timeout(_DB_PROBE_TIMEOUT_SECONDS):
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
    except TimeoutError:
        down = "down (probe timeout)"
        return down, down
    except Exception as exc:
        down = f"down ({type(exc).__name__})"
        return down, down
    pool_info = f"postgres pool (min={settings.DB_POOL_MIN} max={settings.DB_POOL_MAX})"
    return pool_info, ("postgres" if store is not None else "not initialized")


async def _health_body(request: Request, settings: Settings) -> dict:
    gateway = await _gateway_status(settings)
    checkpointer, store = await _persistence_status(request, settings)
    ready = gateway == "ok" and checkpointer.startswith("postgres")
    return {
        "status": "ok" if ready else "degraded",
        "service": settings.PROJECT_NAME,
        "version": settings.APP_VERSION,
        "checks": {
            "llm": f"litellm:{settings.LITELLM_MODEL or '-'}" if settings.LITELLM_MODEL else "missing LITELLM_MODEL",
            "gateway": gateway,
            "checkpointer": checkpointer,
            "store": store,
            "hitl": "enabled" if settings.HITL_ENABLED else "disabled",
        },
    }


@router.get("/health")
async def health(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """Status PENUH (gateway + DB probe, s.d. ~4 dtk).

    Untuk manusia/dashboard. K8s liveness JANGAN ke sini (bisa restart pod
    yang hidup saat downstream gantung) — pakai `/livez`. Lihat T-04.
    """
    return await _health_body(request, settings)


@router.get("/livez")
async def livez(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness MURAH untuk K8s/orchestrator: tanpa I/O eksternal, selalu 200
    bila proses hidup. Kesiapan downstream (DB/gateway) = `/ready`."""
    return {"status": "ok", "service": settings.PROJECT_NAME,
            "version": settings.APP_VERSION}


@router.get("/ready")
async def ready(request: Request, settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Kesiapan untuk load-balancer/K8s: 503 bila belum ready."""
    body = await _health_body(request, settings)
    return JSONResponse(
        status_code=200 if body["status"] == "ok" else 503, content=body)
