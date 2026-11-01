"""Kontrak event SSE. MODELS ONLY — perakitan di app/helpers/response.py.

Alur chat (v0.1, BEKU): started -> token*/thinking*/tool_start/tool_end -> result | error(terminal).
Terminator = stream close (tidak ada baris `[DONE]` agar parser JSON-per-baris lama tak pecah).
`type` di sini = jenis EVENT, bukan RFC `type`.

Bentuk WIRE (di luar model ini): controller membungkus tiap event dengan
envelope transport `{"thread_id": ..., "request_id": ..., **event}` —
konsumen strict-schema: parse envelope dulu, baru validasi event di dalamnya.

Pengecualian jujur: kegagalan PRE-RUN (400/403/404 validasi thread/agent,
init gagal) menghasilkan SATU event `error` TANPA `started` — graph belum
jalan sehingga tak ada stream yang "dimulai". Bila `started` sudah keluar,
penutup TEPAT SATU `result` | `error` dijamin.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus


class StreamStarted(BaseModel):
    type: Literal["started"] = "started"


class StreamToken(BaseModel):
    """Delta teks jawaban. FE: append ke bubble jawaban."""

    type: Literal["token"] = "token"
    delta: str = ""


class StreamThinking(BaseModel):
    """Delta reasoning/thinking. FE: tampilkan collapsible terpisah, JANGAN gabung ke jawaban.

    Diisi dari reasoning model via ChatLiteLLM (v0.2). Model non-reasoning
    tidak memancarkan event ini (normal, bukan error).
    """

    type: Literal["thinking"] = "thinking"
    delta: str = ""


class StreamToolStart(BaseModel):
    """LLM mulai panggil tool. FE: tampilkan indikator (mis. '⏳ cek stok...')."""

    type: Literal["tool_start"] = "tool_start"
    tool: str = Field(examples=["check_stock"])
    input: dict[str, Any] = Field(default_factory=dict)


class StreamToolEnd(BaseModel):
    """Tool selesai. Output tool SENGAJA tidak dibawa (hemat payload; hasil akhir ada di result)."""

    type: Literal["tool_end"] = "tool_end"
    tool: str = Field(examples=["check_stock"])


class StreamResult(BaseModel):
    type: Literal["result"] = "result"
    status: JobStatus = "completed"
    output: dict[str, Any] = Field(default_factory=dict)
    interrupt: dict | None = None
    # v0.4.1 (aditif): daftar SEMUA interrupt paralel — {id, value,
    # response_schema}. Klien menjawab per `id` (resume map) bila >1.
    interrupts: list[dict[str, Any]] | None = None


class StreamError(BaseModel):
    """Event terminal saat stream gagal tengah jalan. FE: tampilkan + tawarkan retry
    dengan thread_id yang sama (idempotency). Bentuk field ikut RFC 7807."""

    type: Literal["error"] = "error"
    title: str = Field(examples=["Stream Failed"])
    status: int = Field(examples=[502])
    detail: str = Field(examples=["Upstream terputus di tengah stream."])
    instance: str = Field(default="-", description="request_id pemanggil.")
    code: str = Field(examples=["STREAM_FAILED"])
