"""Route chat: streaming only (SSE). Rel chat -> ChatController.stream()."""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.controllers.ChatController import ChatController
from app.core.dependencies import (
    get_chat_controller,
    get_request_id,
    get_thread_identity,
)
from app.core.logging import get_logger
from app.core.request_id import request_id_ctx
from app.core.threads import ThreadIdentity
from app.schemas.chat import ChatRequest

router = APIRouter(tags=["chat"])
log = get_logger(__name__)


@router.post("/chat/stream")
async def stream_chat(
    req: ChatRequest,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: ChatController = Depends(get_chat_controller),
) -> StreamingResponse:
    request_id = get_request_id(request)
    # Validasi DI SINI (sebelum StreamingResponse): 400/403/404 agent &
    # thread jadi JSON biasa. Di dalam generator sudah terlanjur 200 →
    # klien terima stream terpotong tanpa event terminal (temuan B3).
    # NOTE: req.thread_id TIDAK dimutasi — controller resolve ulang
    # secara idempotent dari req asli.
    ctrl.validate_before_stream(req, identity)

    async def event_gen():
        # Konteks log di dalam generator: body StreamingResponse berjalan
        # SETELAH middleware selesai (reset ctx) — set ulang di sini agar
        # log korelasi tetap membawa request_id (temuan T-05).
        token = request_id_ctx.set(request_id)
        try:
            async for event in ctrl.stream(req, identity, request_id=request_id):
                if await request.is_disconnected():
                    # Klien pergi: berhenti; JANGAN yield setelah putus.
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            # Disconnect = normal, bukan error: redam agar log bersih.
            raise
        except Exception:
            log.exception("SSE event_gen gagal")
            # controller sudah tutup dengan stream_error bila gagal di dalam;
            # raise di sini berarti generator putus — biarkan handler global log.
            raise
        finally:
            request_id_ctx.reset(token)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )
