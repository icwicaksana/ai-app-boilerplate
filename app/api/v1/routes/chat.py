"""Route chat: streaming only (SSE). Rel chat -> ChatController.stream()."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.controllers.ChatController import ChatController
from app.core.dependencies import get_chat_controller, get_request_id
from app.core.logging import get_logger
from app.schemas.chat import ChatRequest

router = APIRouter(tags=["chat"])
log = get_logger(__name__)


@router.post("/chat/stream")
async def stream_chat(
    req: ChatRequest,
    request: Request,
    ctrl: ChatController = Depends(get_chat_controller),
) -> StreamingResponse:
    request_id = get_request_id(request)

    async def event_gen():
        try:
            async for event in ctrl.stream(req, request_id=request_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:
            log.exception("SSE event_gen gagal")
            # controller sudah tutup dengan stream_error bila gagal di dalam;
            # raise di sini berarti generator putus — biarkan handler global log.
            raise

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )
