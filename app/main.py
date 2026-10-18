"""FastAPI factory: create_app() + lifespan (buka pool + setup persistence sekali)."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

# Windows: psycopg async MENOLAK ProactorEventLoop (default Windows).
# Policy ini mencakup jalur asyncio.run/TestClient/pytest. NAMUN uvicorn CLI
# memaksa Proactor via loop_factory eksplisit (uvicorn/loops/asyncio.py) —
# sehingga cara jalan KANONIS adalah `python -m app.main` (lihat __main__ di
# bawah: loop selector dipilih otomatis). Lifespan juga berjaga: Proactor
# terdeteksi → gagal cepat dengan pesan aksi.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.core.logging import get_logger, setup_logging
from app.helpers.response import job_error
from app.infra.phoenix import setup_phoenix_tracing
from app.infra.postgres import (
    close_pool,
    create_pool,
    get_checkpointer,
    get_store,
    open_pool,
    setup_persistence,
)

log = get_logger(__name__)


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """404/405/dll → RFC 7807 (bukan format default FastAPI)."""
    status = int(getattr(exc, "status_code", 500))
    title = "Not Found" if status == 404 else "HTTP Error"
    return JSONResponse(
        status_code=status,
        content=job_error(
            title=title, detail=str(getattr(exc, "detail", exc)),
            status=status, code=f"HTTP_{status}",
            instance=request.headers.get("X-Request-ID", "-"),
        ),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content=job_error(
            title="Internal Error", detail="Terjadi kesalahan internal.",
            status=500, code="INTERNAL_ERROR",
            instance=request.headers.get("X-Request-ID", "-"),
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    # Jaga Proactor (Windows): pool psycopg pasti PoolTimeout di atasnya.
    # Terjadi bila server dijalankan TANPA --loop asyncio:SelectorEventLoop.
    _proactor = getattr(asyncio, "ProactorEventLoop", None)
    if _proactor is not None and isinstance(asyncio.get_running_loop(), _proactor):
        raise RuntimeError(
            "Event loop Proactor terdeteksi — psycopg async menolaknya. "
            "Jalankan server via `python -m app.main` (loop otomatis benar), "
            "atau uvicorn CLI dengan flag `--loop asyncio:SelectorEventLoop`."
        )
    # v0.3+: FAIL-FAST. DB adalah kebutuhan wajib — jangan start setengah jadi
    # (data hilang diam-diam lebih buruk dari error jelas di depan).
    pool = create_pool(settings)  # raise RuntimeError actionable bila URL kosong
    try:
        await open_pool(pool)
    except Exception as exc:
        raise RuntimeError(
            "Postgres app-db tak terjangkau. Jalankan "
            "`docker compose up -d app-db` di folder Infra/ dan pastikan "
            f"DATABASE_URL benar. Detail: {type(exc).__name__}: {exc}"
        ) from exc
    checkpointer = get_checkpointer(pool)
    store = get_store(pool)
    try:
        await setup_persistence(checkpointer, store)  # idempotent
    except Exception as exc:
        await close_pool(pool)  # jangan bocorkan pool saat startup gagal
        raise RuntimeError(
            "Setup skema Postgres gagal. Pastikan user DB punya hak CREATE "
            f"dan app-db sehat (`docker compose ps app-db` di Infra/). "
            f"Detail: {type(exc).__name__}: {exc}"
        ) from exc
    app.state.pool = pool
    app.state.checkpointer = checkpointer
    app.state.store = store
    log.info("persistence ready (pool min=%d max=%d)",
             settings.DB_POOL_MIN, settings.DB_POOL_MAX)
    setup_phoenix_tracing(settings)  # no-op jika disabled/belum install (log di dalam)
    yield
    await close_pool(pool)
    log.info("pool closed")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.PROJECT_NAME, version="v0.3", lifespan=lifespan)
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Request-ID",
                           "X-Tenant-ID", "X-User-ID"],
            expose_headers=["X-Request-ID"],
        )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(v1_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()


if __name__ == "__main__":  # python -m app.main (cara jalan KANONIS, lintas OS)
    import uvicorn

    # Windows: loop selector eksplisit (uvicorn default = Proactor = PoolTimeout).
    # Linux/Mac: "auto" (boleh uvloop bila terinstall — tercepat di sana).
    _loop = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"
    _port = int(os.environ.get("PORT", "8000"))
    _reload = os.environ.get("RELOAD", "true").lower() in ("1", "true", "yes")
    uvicorn.run("app.main:app", host="127.0.0.1", port=_port,
                reload=_reload, loop=_loop)
