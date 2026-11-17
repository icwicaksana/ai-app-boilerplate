"""FastAPI factory: create_app() + lifespan (buka pool + setup persistence sekali)."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Re-export (kompatibilitas impor lama: `from app.main import ...`).
from app.infra.lifespan import ensure_selector_loop  # noqa: F401

from app.api.v1.router import router as v1_router
from app.core.config import Settings, get_settings
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.core.headers import SecurityHeadersMiddleware
from app.core.logging import get_logger, setup_logging
from app.core.request_id import RequestIDMiddleware, resolve_instance
from app.helpers.response import job_error
from app.infra.cache import build_cache
from app.infra.phoenix import setup_phoenix_tracing
from app.infra.postgres import (
    close_pool,
    create_pool,
    get_checkpointer,
    get_store,
    open_pool,
    setup_persistence,
)
from app.infra.users import ensure_schema

log = get_logger(__name__)


_HTTP_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
}


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """404/405/dll → RFC 7807 (bukan format default FastAPI)."""
    status = int(getattr(exc, "status_code", 500))
    title = _HTTP_TITLES.get(status, "HTTP Error")
    return JSONResponse(
        status_code=status,
        content=job_error(
            title=title, detail=str(getattr(exc, "detail", exc)),
            status=status, code=f"HTTP_{status}",
            instance=resolve_instance(request),
        ),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content=job_error(
            title="Internal Error", detail="Terjadi kesalahan internal.",
            status=500, code="INTERNAL_ERROR",
            instance=resolve_instance(request),
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Hormati settings eksplisit create_app(settings) — krusial untuk test
    # deterministik (lifespan tak lewat Depends).
    settings = getattr(app.state, "settings", None) or get_settings()
    setup_logging(settings.LOG_LEVEL)
    # Jaga Proactor (Windows): pool psycopg pasti PoolTimeout di atasnya.
    # Terjadi bila server dijalankan tanpa entrypoint kanonis
    # `python -m app.main` (yang memilih loop selector otomatis).
    _proactor = getattr(asyncio, "ProactorEventLoop", None)
    if _proactor is not None and isinstance(asyncio.get_running_loop(), _proactor):
        raise RuntimeError(
            "Event loop Proactor terdeteksi — psycopg async menolaknya. "
            "Jalankan server via `python -m app.main` (entrypoint kanonis: "
            "loop selector dipilih otomatis di Windows)."
        )
    # v0.3+: FAIL-FAST. DB adalah kebutuhan wajib — jangan start setengah jadi
    # (data hilang diam-diam lebih buruk dari error jelas di depan).
    pool = create_pool(settings)  # raise RuntimeError actionable bila URL kosong
    checkpointer = store = None
    try:
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
            raise RuntimeError(
                "Setup skema Postgres gagal. Pastikan user DB punya hak CREATE "
                f"dan app-db sehat (`docker compose ps app-db` di Infra/). "
                f"Detail: {type(exc).__name__}: {exc}"
            ) from exc
        # v0.4: tabel user (auth). Gagal = fail-fast (auth butuh SoR).
        try:
            await ensure_schema(pool)
        except Exception as exc:
            raise RuntimeError(
                "Setup tabel auth (app_users) gagal. Pastikan user DB punya "
                f"hak CREATE di schema public. Detail: {type(exc).__name__}: {exc}"
            ) from exc
        # v0.6: tabel approvals + audit trail (HITL).
        try:
            from app.infra.approvals import ensure_schema as ensure_hitl_schema

            await ensure_hitl_schema(pool)
        except Exception as exc:
            raise RuntimeError(
                "Setup tabel HITL (approvals/audit_log) gagal. Pastikan user DB "
                f"punya hak CREATE. Detail: {type(exc).__name__}: {exc}"
            ) from exc
        # v0.7: migrasi 007 RAG (documents/jobs/chunks/konektor + RLS + HNSW).
        # Idempotent + advisory lock (aman 2 entrypoint start bersamaan).
        try:
            from app.infra.rag_migrate import apply_rag_migration

            applied = await apply_rag_migration(pool)
            if applied:
                log.info("migrasi RAG 007 teraplikasi sekarang")
        except Exception as exc:
            raise RuntimeError(
                "Migrasi RAG 007 gagal. Pastikan image app-db = pgvector "
                "(lihat runbook T07: dump→volume-baru→restore) dan user DB "
                f"punya hak CREATE EXTENSION. Detail: {type(exc).__name__}: {exc}"
            ) from exc
        # Assignment di DALAM try (F-05): raise di antara assignment dan
        # yield tak lagi bocorkan pool (except luar menutup 1x).
        app.state.pool = pool
        app.state.checkpointer = checkpointer
        app.state.store = store
    except Exception:
        await close_pool(pool)  # jangan bocorkan koneksi parsial saat startup gagal
        raise
    log.info("persistence ready (pool min=%d max=%d)",
             settings.DB_POOL_MIN, settings.DB_POOL_MAX)
    # --- v0.4: cache (Valkey). STARTUP = fail-fast; RUNTIME = fail-open. ---
    # CACHE_URL kosong → fitur nonaktif (warning jelas, bukan jalur kedua).
    # CACHE_URL diisi tapi tak terjangkau → tolak start (salah config, bukan
    # gangguan sesaat); saat runtime mati, rate-limit fail-open + health down.
    cache = build_cache(settings)
    if cache is not None:
        try:
            await cache.open()
        except Exception as exc:
            raise RuntimeError(
                "Cache (Valkey) tak terjangkau. Jalankan "
                "`docker compose up -d cache` di folder Infra/ dan pastikan "
                f"CACHE_URL benar. Detail: {type(exc).__name__}: {exc}"
            ) from exc
        log.info("cache ready (valkey)")
    app.state.cache = cache
    # v0.5a: turn log resumable (producer terpisah dari koneksi klien).
    from app.core.turns import TurnManager
    from app.infra.turnlog import TurnLog

    app.state.turns = TurnManager(TurnLog(cache))
    log.info("turn log %s", "ready (valkey streams)" if cache else "NONAKTIF")
    # v0.7: klien RAG bersama (embed/rerank/qdrant/tokenizer + validasi backend).
    # RAG_EMBED_BASE_URL kosong = RAG nonaktif (None, bukan error); diisi tapi
    # rusak = tolak start bila RAG_STRICT_STARTUP (default, ADR-12), atau
    # degraded (rag=None, chat non-RAG tetap jalan) bila false (P1-10).
    from app.infra.rag.factory import init_rag

    try:
        rag = await init_rag(settings, pool)
    except RuntimeError:
        if settings.RAG_STRICT_STARTUP:
            await close_pool(pool)
            if cache is not None:
                await cache.close()
            raise
        log.warning("RAG gagal init — degraded (RAG_STRICT_STARTUP=false)")
        rag = None
    app.state.rag = rag
    # v0.9 F2: registry skill dimuat SEKALI di sini (tanpa hot-reload).
    # Saat SKILLS_ENABLED=false hasilnya registry kosong dan perilaku sistem
    # identik dengan v0.8.3.
    from app.skills.boot import SkillStartupError, build_registry

    try:
        app.state.skills = build_registry(settings)
    except SkillStartupError as exc:
        log.error("%s", exc)
        # Samakan pola RAG di atas: tutup yang sudah dibuka sebelum raise,
        # kalau tidak restart lifespan menumpuk koneksi.
        await close_pool(pool)
        if cache is not None:
            await cache.close()
        raise
    if app.state.skills:
        log.info("skills siap: %d skill, manifest=%s",
                 len(app.state.skills), app.state.skills.manifest_id[:12])
    elif getattr(app.state.skills, "errors", ()):
        # Registry falsy TAPI punya error = enabled namun degraded, BUKAN
        # nonaktif. Membedakan keduanya penting untuk diagnosis startup.
        log.warning("skills degraded: %d error (%s)",
                    len(app.state.skills.errors),
                    "; ".join(app.state.skills.errors[:3]))
    else:
        log.info("skills NONAKTIF (SKILLS_ENABLED=false atau tanpa skill)")
    if rag is not None:
        log.info("rag ready (backend=%s, embed=%s dim=%d)",
                 settings.RAG_BACKEND, settings.RAG_EMBED_MODEL, rag.dims)
    else:
        log.info("rag NONAKTIF (RAG_EMBED_BASE_URL kosong)")
    # Peringatan operasional eksplisit (bukan diam-diam):
    # - auth: apakah benar-benar bisa melayani request (v0.8.1: hanya JWT).
    # - LLM lazy-by-design: proxy boleh menyusul, tapi chat akan 502.
    if not settings.auth_configured:
        log.warning("AUTH_MODE=jwt tapi JWT_SECRET kosong — endpoint butuh "
                    "identitas akan menolak (503 AUTH_NOT_CONFIGURED). "
                    "Isi JWT_SECRET (min 32 char) di .env.")
    else:
        log.info("auth ready (mode=jwt, access_ttl=%ss)",
                 settings.ACCESS_TOKEN_TTL_SECONDS)
    if settings.RATE_LIMIT_ENABLED and cache is None:
        log.warning("RATE_LIMIT_ENABLED=true tapi CACHE_URL kosong — "
                    "rate-limit & idempotency TIDAK berjalan.")
    if not settings.LITELLM_API_KEY.strip() or not settings.LITELLM_MODEL.strip():
        log.warning("LLM belum terkonfigurasi (LITELLM_API_KEY/MODEL kosong) — "
                    "server tetap jalan (lazy-by-design), chat akan 502 "
                    "sampai proxy siap.")
    try:
        setup_phoenix_tracing(settings)  # no-op jika disabled/belum install (log di dalam)
    except Exception:
        # Observability tak boleh merobohkan startup (temuan F-11).
        log.exception("phoenix tracing gagal init (lanjut tanpa tracing)")
    try:
        yield
    finally:
        # Shutdown BERSIH walau generator lifespan terima exception.
        try:
            rag = getattr(app.state, "rag", None)
            if rag is not None:
                await rag.aclose()
                log.info("rag clients closed")
        except Exception:
            log.exception("gagal menutup klien RAG saat shutdown")
        try:
            await app.state.turns.cancel_all()
        except Exception:
            log.exception("gagal membatalkan turn producer saat shutdown")
        if cache is not None:
            await cache.close()
            log.info("cache closed")
        await close_pool(pool)
        log.info("pool closed")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Factory app. `settings` opsional (dipakai test agar deterministik)."""
    settings = settings or get_settings()
    # Docs gating (v0.4): mati default; prod mewajibkan mati (divalidasi config).
    docs_on = settings.ENABLE_DOCS
    app = FastAPI(
        title=settings.PROJECT_NAME, version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    # Kunci settings ke instance ini (lifespan + Depends). Tanpa ini settings
    # eksplisit test diam-diam diabaikan (temuan uji live v0.7).
    app.state.settings = settings
    app.dependency_overrides[get_settings] = lambda: settings
    origins = settings.cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=settings.cors_methods(),
            allow_headers=settings.cors_headers(),
            expose_headers=["X-Request-ID"],
        )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware,
                       hsts=(settings.ENVIRONMENT == "prod"))
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(v1_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()


if __name__ == "__main__":  # python -m app.main (cara jalan KANONIS, lintas OS)
    import uvicorn

    ensure_selector_loop()
    boot = get_settings()
    # Loop programmatic "asyncio:SelectorEventLoop" VALID di Windows
    # (uvicorn resolve ke kelas loop; lifecycle guard memverifikasi).
    # Linux/Mac: "auto" (boleh uvloop bila terinstall — tercepat di sana).
    _loop = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"
    uvicorn.run("app.main:app", host=boot.HOST, port=boot.PORT,
                reload=boot.RELOAD, loop=_loop)
