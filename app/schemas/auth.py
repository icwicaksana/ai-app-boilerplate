"""Kontrak auth (v0.4): login, refresh, logout, me. MODELS ONLY."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

WORKSPACE_PATTERN = r"^[a-z0-9-]{3,32}$"
USER_PATTERN = r"^[a-z0-9-_]{1,64}$"


class LoginRequest(BaseModel):
    workspace: str = Field(pattern=WORKSPACE_PATTERN, examples=["toko-a"])
    username: str = Field(pattern=USER_PATTERN, examples=["budi"])
    password: str = Field(min_length=1, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Umur access token (detik).")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    """Opsional: ikut revoke refresh token (best-effort)."""

    refresh_token: str | None = None


class MeResponse(BaseModel):
    workspace: str
    user: str
    scopes: list[str] = Field(default_factory=list)
    expires_at: int = Field(description="Epoch detik kedaluwarsa access token.")
