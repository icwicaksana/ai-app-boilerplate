"""FastAPI factory: create_app() + lifespan (setup checkpointer sekali)."""

from __future__ import annotations

from contextlib import asynccontextmanager

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
from app.infra.postgres import get_checkpointer, setup_checkpointer

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
    try:
        checkpointer = get_checkpointer(settings.DATABASE_URL)
        await setup_checkpointer(checkpointer)
        app.state.checkpointer = checkpointer
        log.info("checkpointer ready")
    except Exception as exc:  # jangan gagalkan startup — health laporkan degraded
        log.warning("checkpointer setup gagal (lanjut mode degraded): %s", exc)
    setup_phoenix_tracing(settings)  # no-op jika disabled/belum install (log di dalam)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.PROJECT_NAME, version="v0.2", lifespan=lifespan)
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(v1_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
