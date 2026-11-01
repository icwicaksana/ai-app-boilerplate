"""Kontrak API approval + audit (v0.6). MODELS ONLY."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ApprovalResponse(BaseModel):
    id: int
    tenant: str
    thread_id: str
    interrupt_id: str
    agent: str
    tool_name: str
    action_hash: str
    action_snapshot: dict[str, Any] = Field(default_factory=dict)
    policy_version: str
    status: str
    expires_at: str | None = None
    created_at: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    decision: str | None = None
    reason: str | None = None


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class DecideRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


class DecideResponse(BaseModel):
    approval_id: int
    status: str
    resumed: bool
    result: dict[str, Any] | None = None


class AuditItem(BaseModel):
    id: int
    actor: str
    action: str
    subject_type: str
    subject_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str = "-"
    hash: str
    prev_hash: str
    created_at: str | None = None


class AuditListResponse(BaseModel):
    items: list[AuditItem] = Field(default_factory=list)
    limit: int = 100
    offset: int = 0


class VerifyChainResponse(BaseModel):
    ok: bool
    first_bad_id: int | None = None
