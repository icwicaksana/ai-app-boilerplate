"""Dependency injection: satu-satunya tempat wiring settings/persistence/controller.

- Pool/checkpointer/store DIBUAT lifespan (app.state), BUKAN di sini. Alasan:
  pool hanya boleh ada 1 per worker; dibuat per request = bocor koneksi.
- Rel chat: route /chat/stream -> Depends(get_chat_controller) -> ChatController.
- Rel pipeline: route /jobs/{agent} -> Depends(get_job_controller) -> JobController.
- Rel state: route /threads/* -> Depends(get_thread_controller) -> ThreadController.
"""

from fastapi import Depends, Request

from app.controllers.ChatController import ChatController
from app.controllers.JobController import JobController
from app.controllers.runner import Runner
from app.core.config import Settings, get_settings
from app.core.errors import InvalidIdentityError, InvalidThreadIdError, StorageUnavailableError
from app.core.request_id import request_id_ctx, resolve_instance
from app.core.threads import ThreadIdentity, canon_segment


def get_runner() -> Runner:
    return Runner()


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


def get_thread_identity(request: Request) -> ThreadIdentity:
    """Satu sumber identitas: header tepercaya (v0.3) → klaim JWT (v0.4).

    Kosong/format salah = 400 eksplisit (bukan 500, bukan default diam-diam).

    Normalisasi (strip/lowercase) HANYA di `canon_segment` (threads.py) —
    fungsi ini meneruskan header mentah apa adanya; error identitas tetap
    berkode INVALID_IDENTITY (kontrak beku) meski validasi berbagi pola.
    """
    try:
        tenant = canon_segment(request.headers.get("X-Tenant-ID"), "tenant")
        user = canon_segment(request.headers.get("X-User-ID"), "user")
    except InvalidThreadIdError as exc:
        raise InvalidIdentityError(exc.message) from exc
    return ThreadIdentity(tenant=tenant, user=user)


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


def get_chat_controller(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ChatController:
    checkpointer, store = _persistence(request)
    return ChatController(settings, checkpointer, get_runner(), store=store)


def get_job_controller(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JobController:
    checkpointer, _ = _persistence(request)
    return JobController(settings, checkpointer, get_runner())


def get_thread_controller(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Rel state/history (v0.3). Import lazy agar modul tetap ringan."""
    from app.controllers.ThreadController import ThreadController

    checkpointer, _ = _persistence(request)
    return ThreadController(settings, checkpointer, get_runner())
