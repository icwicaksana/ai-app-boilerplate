"""Dependency injection: satu-satunya tempat wiring settings/persistence/controller.

- Pool/checkpointer/store/cache DIBUAT lifespan (app.state), BUKAN di sini.
  Alasan: pool hanya boleh ada 1 per worker; dibuat per request = bocor koneksi.
- Rel chat: route /chat/stream -> Depends(get_chat_controller) -> ChatController.
- Rel pipeline: route /jobs/{agent} -> Depends(get_job_controller) -> JobController.
- Rel state: route /threads/* -> Depends(get_thread_controller) -> ThreadController.
- Rel auth: Depends(get_thread_identity) / Depends(get_current_claims) (v0.4).
"""

from fastapi import Depends, Request

from app.controllers.ChatController import ChatController
from app.controllers.JobController import JobController
from app.controllers.runner import Runner
from app.core.auth import resolve_claims, resolve_identity
from app.core.config import Settings, get_settings
from app.core.errors import StorageUnavailableError
from app.core.request_id import request_id_ctx, resolve_instance
from app.core.security import TokenClaims
from app.core.threads import ThreadIdentity
from app.core.turns import TurnManager
from app.infra.cache import CacheClient
from app.infra.turnlog import TurnLog


def get_runner() -> Runner:
    """Runner dengan durability dari Settings (sync untuk alur HITL)."""
    try:
        durability = get_settings().GRAPH_DURABILITY
    except Exception:  # config rusak → default aman
        durability = "async"
    return Runner(durability=durability)


def get_request_id(request: Request) -> str:
    """Satu sumber request_id: state middleware → echo header → generate.

    Juga sinkronkan ContextVar log agar log jalur sync membawa ID yang sama.
    """
    rid = getattr(getattr(request, "state", None), "request_id", None)
    if not rid:
        rid = resolve_instance(request)
    else:
        rid = str(rid)
    try:
        request.state.request_id = rid
    except Exception:
        pass
    request_id_ctx.set(rid)
    return rid


def get_cache(request: Request) -> CacheClient | None:
    """Cache dari lifespan; None = fitur cache nonaktif (ADR-15)."""
    return getattr(request.app.state, "cache", None)


def get_turn_manager(request: Request) -> TurnManager:
    """TurnManager dari lifespan (turn log resumable, v0.5a)."""
    manager = getattr(request.app.state, "turns", None)
    if manager is None:  # lifespan tak jalan (test) → manager nonaktif
        manager = TurnManager(TurnLog(None))
    return manager


def get_approval_service(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """ApprovalService (v0.6) dari pool + checkpointer lifespan."""
    from app.core.approvals import ApprovalService

    pool = getattr(request.app.state, "pool", None)
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if pool is None or checkpointer is None:
        raise StorageUnavailableError(
            "Persistence belum siap (lifespan gagal/startup tak jalan).")
    return ApprovalService(settings, pool, checkpointer)
async def get_thread_identity(
    request: Request,
    settings: Settings = Depends(get_settings),
    cache: CacheClient | None = Depends(get_cache),
) -> ThreadIdentity:
    """Identitas pemanggil: JWT (default) atau trust-header (dev, ADR-16).

    Semua route memakai dependency ini; mengganti implementasi di sini
    mengamankan seluruh API tanpa menyentuh controller.
    """
    return await resolve_identity(request, settings, cache)


async def get_current_claims(
    request: Request,
    settings: Settings = Depends(get_settings),
    cache: CacheClient | None = Depends(get_cache),
) -> TokenClaims:
    """Klaim token terverifikasi (dipakai /auth/me, logout, audit)."""
    return await resolve_claims(request, settings, cache)


def _persistence(request: Request) -> tuple:
    """Ambil (checkpointer, store) dari lifespan. Absen = 503 eksplisit."""
    checkpointer = getattr(request.app.state, "checkpointer", None)
    store = getattr(request.app.state, "store", None)
    if checkpointer is None or store is None:
        raise StorageUnavailableError(
            "Persistence belum siap (lifespan gagal/startup tak jalan). "
            "Pastikan DATABASE_URL benar dan app-db hidup, lalu restart server."
        )
    return checkpointer, store


def get_pool(request: Request):
    """Pool Postgres dari lifespan (dipakai auth/users)."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise StorageUnavailableError(
            "Pool Postgres belum siap (lifespan gagal/startup tak jalan).")
    return pool


def get_chat_controller(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ChatController:
    checkpointer, store = _persistence(request)
    return ChatController(settings, checkpointer, get_runner(), store=store,
                          pool=_optional_pool(request),
                          rag=get_rag_clients(request))


def get_job_controller(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JobController:
    checkpointer, _ = _persistence(request)
    return JobController(settings, checkpointer, get_runner(),
                         pool=_optional_pool(request),
                         rag=get_rag_clients(request))


def _optional_pool(request: Request):
    """Pool bila lifespan jalan; None bila tidak (agen non-RAG tetap jalan;
    agen NEEDS_RETRIEVER 503 jelas di controller)."""
    try:
        return get_pool(request)
    except StorageUnavailableError:
        return None


def get_thread_controller(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Rel state/history (v0.3). Import lazy agar modul tetap ringan."""
    from app.controllers.ThreadController import ThreadController

    checkpointer, _ = _persistence(request)
    return ThreadController(settings, checkpointer, get_runner())


def get_rag_clients(request: Request):
    """Bundle klien RAG dari lifespan (None = RAG nonaktif, D6)."""
    return getattr(request.app.state, "rag", None)


def get_document_controller(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Rel dokumen/ingest (v0.7)."""
    from app.controllers.DocumentController import DocumentController

    return DocumentController(settings, get_pool(request),
                              rag_clients=get_rag_clients(request))
