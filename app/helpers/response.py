"""Pembangun respons HTTP — 1 cara merakit untuk semua jalur (Set A: rel + hasil).

- Rel sync (`POST /jobs/...`): job_success() / job_error().
- Rel SSE (`POST /chat/stream`): stream_started() / stream_token() / stream_thinking() /
  stream_tool_start() / stream_tool_end() / stream_success() / stream_error().
- job_error() dan stream_error() berdua delegasi ke _problem() — bentuk RFC 7807
  tetap 1 sumber, tidak pernah ditulis 2x.

ATURAN KERAS:
- Builder TIDAK membungkus envelope baru. Bentuk luar tetap bare (diputuskan bareng).
- job_success hanya untuk SUKSES. Error sync TETAP via exception + handler
  (HTTP status harus benar, bukan 200 berisi error). JANGAN panggil job_success
  dengan data error — itu pola yang sudah kita haramkan.
- Model (bentuk) di app/schemas/...; file ini hanya merakit + validasi.
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from pydantic import BaseModel

from app.schemas.common import ResponseMeta
from app.schemas.stream import (
    StreamError,
    StreamResult,
    StreamStarted,
    StreamThinking,
    StreamToken,
    StreamToolEnd,
    StreamToolStart,
)

TModel = TypeVar("TModel", bound=BaseModel)


def start_timer() -> float:
    return time.perf_counter()


def duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def job_success(data: TModel, *, request_id: str = "-", started: float | None = None) -> TModel:
    """Suntik meta + kembalikan MODEL (FastAPI yang serialkan ke JSON).

    Semua respons sukses sync WAJIB lewat sini agar `meta` selalu ada.
    """
    meta = ResponseMeta(
        request_id=request_id,
        duration_ms=duration_ms(started) if started is not None else None,
    )
    return data.model_copy(update={"meta": meta})


def _problem(
    *,
    title: str,
    detail: str,
    status: int,
    code: str,
    instance: str = "-",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """1 sumber bentuk error RFC 7807. Jangan dipanggil langsung — pakai
    job_error() (sync) atau stream_error() (SSE terminal)."""
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code,
    }
    if extra:
        body.update(extra)
    return body


def job_error(
    *,
    title: str,
    detail: str,
    status: int,
    code: str,
    instance: str = "-",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Badan error sync untuk JSONResponse. Dipakai handler di app/core/errors.py."""
    return _problem(title=title, detail=detail, status=status,
                    code=code, instance=instance, extra=extra)


def stream_started() -> dict[str, Any]:
    return StreamStarted().model_dump(mode="json")


def stream_token(delta: str) -> dict[str, Any]:
    return StreamToken(delta=delta or "").model_dump(mode="json")


def stream_thinking(delta: str) -> dict[str, Any]:
    return StreamThinking(delta=delta or "").model_dump(mode="json")


def stream_tool_start(tool: str, input: dict[str, Any] | None = None) -> dict[str, Any]:
    return StreamToolStart(tool=tool, input=dict(input or {})).model_dump(mode="json")


def stream_tool_end(tool: str) -> dict[str, Any]:
    return StreamToolEnd(tool=tool).model_dump(mode="json")


def stream_success(
    *,
    output: dict[str, Any],
    status: str = "completed",
    interrupt: dict[str, Any] | None = None,
    interrupts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return StreamResult(status=status, output=output, interrupt=interrupt,  # type: ignore[arg-type]
                        interrupts=interrupts).model_dump(mode="json")


def stream_error(
    *,
    title: str = "Stream Failed",
    detail: str = "Stream terputus.",
    code: str = "STREAM_FAILED",
    status: int = 500,
    request_id: str = "-",
) -> dict[str, Any]:
    body = _problem(title=title, detail=detail, status=status,
                    code=code, instance=request_id)
    return StreamError(title=body["title"], status=body["status"], detail=body["detail"],
                       instance=body["instance"], code=body["code"]).model_dump(mode="json")
