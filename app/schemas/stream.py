"""Kontrak event SSE. MODELS ONLY — perakitan di app/helpers/response.py.

Alur chat: started -> [token... (v0.1)] -> result | error(terminal).
`type` di sini = jenis EVENT (started/result/error), bukan RFC `type`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus


class StreamStarted(BaseModel):
    type: Literal["started"] = "started"


class StreamResult(BaseModel):
    type: Literal["result"] = "result"
    status: JobStatus = "completed"
    output: dict[str, Any] = Field(default_factory=dict)
    interrupt: dict | None = None


class StreamError(BaseModel):
    """Event terminal saat stream gagal tengah jalan. FE: tampilkan + tawarkan retry
    dengan thread_id yang sama (idempotency). Bentuk field ikut RFC 7807."""

    type: Literal["error"] = "error"
    title: str = Field(examples=["Stream Failed"])
    status: int = Field(examples=[502])
    detail: str = Field(examples=["Upstream terputus di tengah stream."])
    instance: str = Field(default="-", description="request_id pemanggil.")
    code: str = Field(examples=["STREAM_FAILED"])
