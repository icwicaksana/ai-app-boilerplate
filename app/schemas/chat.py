"""Kontrak chatbot: POST /chat/stream (SSE)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus, ResponseMeta, RunMetadata


class ChatRequest(BaseModel):
    agent: str = Field(default="example_chat", examples=["example_chat"])
    thread_id: str | None = Field(
        default=None,
        description=(
            "Format beku v0.3: 'workspace:user:session' (contoh 'toko-a:budi:sess-7f3a2c1e'). "
            "Kosong = server susun dari identitas JWT (klaim workspace+sub). "
            "Wajib milik pemanggil; format lama (thr-xxx) ditolak 400."
        ),
        examples=["toko-a:budi:sess-7f3a2c1e"],
    )
    input: str = Field(min_length=1, max_length=8000, examples=["stok ITEM-99 berapa?"])
    metadata: RunMetadata = Field(default_factory=RunMetadata)


class ChatResponse(BaseModel):
    """Referensi bentuk akhir chat untuk FE (v0 SSE pakai StreamResult).

    DEPRECATED di BE: tidak dipakai route (SSE-only). Disimpan agar FE punya
    acuan field reply/status/interrupt. Jangan hapus sebelum v1.
    """

    thread_id: str
    reply: str
    status: JobStatus = "completed"
    model: str | None = Field(default=None, examples=["gpt-4o-mini"])
    interrupt: dict | None = Field(
        default=None,
        description="Terisi hanya jika status=pending_approval (HITL v1). v0 selalu null.",
    )
    meta: ResponseMeta = Field(default_factory=ResponseMeta)
