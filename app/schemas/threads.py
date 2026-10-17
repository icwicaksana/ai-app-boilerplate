"""Kontrak API thread: state/history/fork/delete (BEKU sejak v0.3).

MODELS ONLY — perakitan di ThreadController. Perubahan bentuk = bump major.

Catatan desain:
- `next` (node berikut) SENGAJA absen: hanya tersedia via graph, sedangkan
  endpoint ini bekerja di level checkpointer (tanpa tahu agen pemilik thread).
- `created_at` = `ts` checkpoint (ISO string dari LangGraph).
- Isi pesan di-cap 2000 char per pesan (`truncated=true` bila terpotong).
- Pesan infrastruktur (profil user) DISEMBUNYIKAN dari preview + count.
- `messages_count` = jumlah pesan yang DIKEMBALIKAN (sama dengan total bila
  tanpa `last_n`; bila `last_n` dipakai, ini jumlah potongan terakhir).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ThreadMessagePreview(BaseModel):
    role: str = Field(examples=["user", "assistant", "system", "tool"])
    content: str = ""
    truncated: bool = False
    tool: str | None = Field(default=None, description="Nama tool (hanya role=tool).")


class ThreadStateResponse(BaseModel):
    thread_id: str
    checkpoint_id: str
    step: int | None = None
    source: str | None = None
    created_at: str | None = None
    messages_count: int = 0
    messages: list[ThreadMessagePreview] = Field(default_factory=list)


class HistoryItem(BaseModel):
    checkpoint_id: str
    step: int | None = None
    source: str | None = None
    created_at: str | None = None
    messages_count: int = 0


class HistoryResponse(BaseModel):
    thread_id: str
    items: list[HistoryItem] = Field(default_factory=list)
    limit: int
    offset: int
    total: int


class ForkRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1, examples=["1f029ca3-1f5b-6704-8004-820c16b69a5a"])
    agent: str = Field(
        min_length=1,
        description="Nama agen pemilik thread (registry) — dipakai untuk tulis state awal.",
        examples=["example_chat"],
    )


class ForkResponse(BaseModel):
    thread_id: str = Field(description="Thread BARU (tenant:user:sess-*) hasil fork.")
    forked_from: str = Field(description="checkpoint_id sumber.")
    agent: str
