"""Skema API dokumen/ingest RAG (D12)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Preset = Literal["prose", "markdown", "pdf", "factoid"]
OcrFlag = Literal["auto", "on", "off"]


class IngestEnqueueResponse(BaseModel):
    document_id: str
    job_id: int
    status: str = "queued"
    deduped: bool = False  # True bila file sama sudah ada (no-op)


class DocumentResponse(BaseModel):
    document_id: str
    title: str
    source_type: str
    status: str
    chunk_preset: str
    bytes: int
    chunks: int = 0
    job_id: int | None = None
    last_error: str | None = None
    created_at: datetime | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    meta: dict[str, Any] = Field(default_factory=dict)


class JobStatusResponse(BaseModel):
    job_id: int
    document_id: str
    state: str
    stage: str
    progress: int
    attempts: int
    last_error: str | None = None


class RetryRequest(BaseModel):
    ocr: OcrFlag | None = None  # override flag OCR (mis. pindai → on)


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)
    filters: dict[str, str] | None = None


class CitationResponse(BaseModel):
    n: int
    doc_id: str
    chunk_id: str
    source: str
    page: int | None = None
    score: float
    backend: str


class RetrievalSearchResponse(BaseModel):
    matches: list[CitationResponse]
    meta: dict[str, Any] = Field(default_factory=dict)
