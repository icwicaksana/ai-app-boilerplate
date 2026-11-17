"""Route threads: inspeksi state/history, fork, hapus (rel state, v0.3).

- `thread_id` di path mengandung `:` — FE wajib URL-encode (`%3A`).
  Contoh: /threads/toko-a%3Abudi%3Asess-7f3a/state
- Semua endpoint butuh Bearer JWT (identitas dari klaim workspace + sub).
"""

from fastapi import APIRouter, Depends, Query, Request, Response

from app.controllers.ThreadController import ThreadController
from app.core.dependencies import (
    get_request_id,
    get_thread_controller,
    get_thread_identity,
    require_scope_dep,
)
from app.core.threads import ThreadIdentity
from app.schemas.threads import ForkRequest, ForkResponse, HistoryResponse, ThreadStateResponse

from app.core.ratelimit import rate_limit_dependency

router = APIRouter(tags=["threads"],
                   dependencies=[Depends(require_scope_dep("threads")),
                                   Depends(rate_limit_dependency(
                                       "threads", "RATE_LIMIT_THREADS_PER_MIN"))])


@router.get("/threads/{thread_id}/state", response_model=ThreadStateResponse)
async def thread_state(
    thread_id: str,
    request: Request,
    last_n: int | None = Query(default=None, ge=1, le=200,
                               description="Batasi N pesan TERAKHIR (default: semua)."),
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: ThreadController = Depends(get_thread_controller),
) -> ThreadStateResponse:
    return await ctrl.get_state(identity, thread_id, last_n=last_n,
                                request_id=get_request_id(request))


@router.get("/threads/{thread_id}/history", response_model=HistoryResponse)
async def thread_history(
    thread_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: ThreadController = Depends(get_thread_controller),
) -> HistoryResponse:
    return await ctrl.get_history(
        identity, thread_id, limit=limit, offset=offset,
        request_id=get_request_id(request),
    )


@router.post("/threads/{thread_id}/fork", response_model=ForkResponse)
async def thread_fork(
    thread_id: str,
    req: ForkRequest,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: ThreadController = Depends(get_thread_controller),
) -> ForkResponse:
    return await ctrl.fork(
        identity, thread_id, req.checkpoint_id, req.agent,
        request_id=get_request_id(request),
    )


@router.delete("/threads/{thread_id}", status_code=204)
async def thread_delete(
    thread_id: str,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: ThreadController = Depends(get_thread_controller),
) -> Response:
    await ctrl.delete_thread(identity, thread_id, request_id=get_request_id(request))
    return Response(status_code=204)
