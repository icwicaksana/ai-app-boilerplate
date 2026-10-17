"""Kontrak one-shot agent: POST /jobs/{agent_name} (tanpa chat loop)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus, ResponseMeta, RunMetadata


class JobRequest(BaseModel):
    thread_id: str | None = Field(
        default=None,
        description=(
            "Format beku v0.3: 'tenant:user:session'. Kosong = server susun dari "
            "header X-Tenant-ID/X-User-ID. Wajib milik pemanggil (403 bila asing)."
        ),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"file_url": "https://example.com/invoice.pdf"}],
    )
    metadata: RunMetadata = Field(default_factory=RunMetadata)


class JobResponse(BaseModel):
    thread_id: str
    status: JobStatus = "completed"
    output: dict[str, Any] = Field(
        default_factory=dict,
        description="v0 (chat-based agent): {'reply': '...'}. v1 (StateGraph murni): output domain.",
    )
    interrupt: dict | None = None
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class ResumeRequest(BaseModel):
    approved: bool = Field(examples=[True])
    values: dict[str, Any] = Field(default_factory=dict)


class ResumeResponse(BaseModel):
    thread_id: str
    status: JobStatus = "completed"
    output: dict[str, Any] = Field(default_factory=dict)
