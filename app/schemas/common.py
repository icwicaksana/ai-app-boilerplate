"""Kontrak umum: error + metadata. Frontend boleh pegang file ini sebagai acuan."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Acuan bentuk error (RFC 7807 + extension `code`). Lihat app/core/errors.py."""

    type: str = Field(default="about:blank")
    title: str = Field(examples=["Agent Not Found"])
    status: int = Field(examples=[404], description="SELALU == HTTP status asli.")
    detail: str = Field(examples=["Agent 'x' tidak terdaftar."])
    instance: str = Field(examples=["coba-1"], description="request_id pemanggil.")
    code: str = Field(examples=["AGENT_NOT_FOUND"])


class ResponseMeta(BaseModel):
    """Meta tiap respons sukses: tracing + observability. FE boleh tampilkan/tidak."""

    request_id: str = Field(default="-", description="Gema X-Request-ID untuk lacak log.")
    duration_ms: int | None = Field(default=None, description="Lama proses di server.")


class RunMetadata(BaseModel):
    """Slot observability + multi-workspace (v1). v0: bebas diisi, tidak wajib."""

    source: str | None = Field(default=None, examples=["dashboard", "webhook", "cron"])
    workspace_id: str | None = Field(default=None, description="Slot v1: isolasi antar toko.")
    user_id: str | None = Field(default=None, description="Slot v1: user pemanggil.")
    extra: dict[str, Any] = Field(default_factory=dict)


JobStatus = Literal["completed", "pending_approval", "failed"]
