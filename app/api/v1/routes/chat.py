"""Route chat: SSE streaming + resume turn-level (v0.5a) + hook approval (v0.6).

Dua jalur:
1. `POST /chat/stream` — buat turn, jalankan producer di latar, tail log.
2. `GET /chat/stream/{turn_id}` — resume: replay dari `Last-Event-ID` lalu
   ikuti sampai event terminal (atau 410 bila turn kedaluwarsa).

Kalau cache nonaktif (`CACHE_URL` kosong), jalur 1 tetap melayani streaming
langsung tanpa resume (degradasi eksplisit, bukan diam-diam).
"""

import asyncio
import json
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from app.controllers.ChatController import ChatController
from app.core.approvals import record_pending_approvals
from app.core.dependencies import (
    get_chat_controller,
    get_request_id,
    get_thread_identity,
    get_turn_manager,
    require_scope_dep,
)
from app.core.errors import StreamGoneError
from app.core.logging import get_logger
from app.core.ratelimit import rate_limit_dependency
from app.core.request_id import request_id_ctx
from app.core.threads import ThreadIdentity
from app.core.turns import TurnManager
from app.infra.turnlog import new_turn_id
from app.schemas.chat import ChatRequest
from app.schemas.stream import StreamStarted

router = APIRouter(tags=["chat"],
                     dependencies=[Depends(require_scope_dep("chat"))])
log = get_logger(__name__)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


def _sse(entry_id: str, event: dict) -> str:
    """Satu frame SSE: `id:` = cursor stream (untuk Last-Event-ID)."""
    return f"id: {entry_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _approval_hook(request: Request, identity: ThreadIdentity, thread_id: str,
                   agent: str, request_id: str) -> OnEvent:
    """Hook: interrupt → baris approval pending (v0.6)."""
    async def _hook(event: dict[str, Any]) -> None:
        if event.get("type") != "result" or event.get("status") != "pending_approval":
            return
        await record_pending_approvals(
            request, identity=identity, thread_id=thread_id, agent=agent,
            interrupts=event.get("interrupts") or [], request_id=request_id)
    return _hook


@router.post("/chat/stream")
async def stream_chat(
    req: ChatRequest,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: ChatController = Depends(get_chat_controller),
    turns: TurnManager = Depends(get_turn_manager),
    _rl=Depends(rate_limit_dependency("chat", "RATE_LIMIT_CHAT_PER_MIN")),
) -> StreamingResponse:
    request_id = get_request_id(request)
    # Validasi SEBELUM StreamingResponse: 400/403/404 jadi JSON biasa.
    thread_id = ctrl.validate_before_stream(req, identity)
    on_event = _approval_hook(request, identity, thread_id, req.agent, request_id)

    if not turns.enabled:  # cache nonaktif → streaming langsung (tanpa resume)
        return _direct_stream(req, identity, ctrl, request_id, on_event)

    turn_id = new_turn_id()
    await turns.start(
        workspace=identity.workspace, turn_id=turn_id, owner=identity.user,
        first_event=StreamStarted(turn_id=turn_id).model_dump(mode="json"),
        events=ctrl.stream(req, identity, request_id=request_id))

    async def tail():
        token = request_id_ctx.set(request_id)
        try:
            async for entry_id, event in turns.follow(
                    workspace=identity.workspace, turn_id=turn_id):
                if await request.is_disconnected():
                    break
                await on_event(event)
                yield _sse(entry_id, event)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            log.exception("SSE tail gagal turn=%s", turn_id)
            raise
        finally:
            request_id_ctx.reset(token)

    return StreamingResponse(
        tail(), media_type="text/event-stream",
        headers={**_SSE_HEADERS, "X-Request-ID": request_id,
                 "X-Turn-ID": turn_id})


@router.get("/chat/stream/{turn_id}")
async def resume_stream(
    turn_id: str,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    turns: TurnManager = Depends(get_turn_manager),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Resume turn: replay dari cursor klien lalu lanjut sampai terminal."""
    if not turns.enabled or not await turns.turnlog.exists(
            workspace=identity.workspace, turn_id=turn_id):
        raise StreamGoneError(turn_id)
    # v1.0 S3: pemilik diverifikasi; salah-pemilik = 410 SAMA seperti turn
    # hilang (tanpa oracle keberadaan). Owner tak ada (turn pra-v1.0) pun
    # 410 — fail-closed; turn hidup ≤15 mnt jadi jendela deploy sempit.
    if not await turns.is_owner(workspace=identity.workspace, turn_id=turn_id,
                                user=identity.user):
        raise StreamGoneError(turn_id)
    cursor = (last_event_id or "0-0").strip() or "0-0"

    async def tail():
        async for entry_id, event in turns.follow(
                workspace=identity.workspace, turn_id=turn_id, cursor=cursor):
            if await request.is_disconnected():
                break
            yield _sse(entry_id, event)

    return StreamingResponse(
        tail(), media_type="text/event-stream",
        headers={**_SSE_HEADERS, "X-Turn-ID": turn_id})


def _direct_stream(req: ChatRequest, identity: ThreadIdentity,
                   ctrl: ChatController, request_id: str,
                   on_event: OnEvent) -> StreamingResponse:
    """Fallback tanpa cache: perilaku v0.4 (tak bisa resume)."""

    async def event_gen():
        token = request_id_ctx.set(request_id)
        try:
            async for event in ctrl.stream(req, identity, request_id=request_id):
                if await request.is_disconnected():
                    break
                await on_event(event)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            log.exception("SSE event_gen gagal")
            raise
        finally:
            request_id_ctx.reset(token)

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={**_SSE_HEADERS, "X-Request-ID": request_id})
