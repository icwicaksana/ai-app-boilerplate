"""Port RAG v0.7 (KONTRAK BEKU — desain: Docs/RAG-V07-DESIGN.md D1).

Aturan modul (test arsitektur `test_rag_arch.py`):
- File ini + seluruh `app/rag/` DILARANG mengimpor `app/infra`, `httpx`,
  `qdrant_client`, `psycopg`, `fastapi`, atau SDK model. Pure Python + pydantic.
- Tenant dikunci saat CONSTRUCTION (factory-bound, D2) — tidak ada argumen
  tenant di method query. Implementasi yang menerima tenant per-call = langgar kontrak.

Lapis:
- `EmbeddingsPort` / `RerankerPort` — jasa model (adapter: Infinity).
- `VectorStorePort` — primitif per-backend (adapter: pgvector / Qdrant).
  `RAG_BACKEND` mengganti INI, bukan seluruh retrieval.
- `BoundRetriever` — hasil `RetrieverFactory.for_tenant()`; dipakai agen.
- `SourceConnector` / `OCREngine` / `DocumentParser` — ingest (D10/D11).
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict


# --------------------------------------------------------------------------- DTO

class CitedChunk(TypedDict):
    """Satu hasil retrieval. `score` = skor final pasca-RRF/rerank (ordinal)."""

    chunk_id: str
    doc_id: str
    content: str
    source: str            # nama file/URI manusiawi
    page: int | None
    sheet: NotRequired[str | None]      # XLSX (K6)
    heading: NotRequired[str | None]    # MD (K6)
    score: float
    backend: str           # "pgvector" | "qdrant" — observability, bukan branching
    metadata: dict[str, Any]


class ChunkIn(TypedDict, total=False):
    """Chunk siap index (datang dari worker ingest)."""

    content: str           # required (total=False + validasi di adapter)
    vector: list[float]
    ord: int
    page: int | None
    sheet: str | None
    heading: str | None
    token_count: int
    metadata: dict[str, Any]


class RerankedDoc(TypedDict):
    index: int             # indeks ke `documents` input
    score: float           # ordinal (bukan probabilitas) — bandingkan, jangan tafsir


class RetrievalResult(TypedDict):
    chunks: list[CitedChunk]
    status: Literal["ok", "no_evidence"]
    degraded: list[str]    # mis. ["rerank_timeout"] — aditif


class IndexInfo(TypedDict):
    """Identitas index backend — untuk validasi anti-campur (D4/D6)."""

    backend: str
    embedding_model: str
    embedding_dim: int
    embedding_version: str
    chunk_count: int | None  # None = backend tak tahu tanpa scan mahal


class OCRPage(TypedDict):
    page: int
    text: str              # teks halaman ini (untuk chunking preset pdf)
    boxes: list[list[float]]


class OCRResult(TypedDict):
    text: str
    confidence: float      # 0..1
    pages: list[OCRPage]
    lang: str


class ParsedPage(TypedDict):
    page: int
    text: str
    headings: list[str]


class ParsedTable(TypedDict):
    page: int | None
    sheet: str | None
    headers: list[str]
    rows: list[list[str]]


class ParsedDocument(TypedDict):
    pages: list[ParsedPage]
    tables: list[ParsedTable]
    is_scanned: bool       # True bila teks terekstrak minim → kandidat OCR (D10)
    parser: str            # nama implementasi (observability)
    metadata: dict[str, Any]


class TableMeta(TypedDict, total=False):
    name: str
    description: str
    columns: list[dict[str, Any]]
    primary_key: list[str]
    row_count: int | None


class SourceRecord(TypedDict, total=False):
    ref: str               # identitas stabil (mis. "orders:12345")
    text: str              # render baris → teks (untuk chunking)
    watermark: str         # nilai (updated_at,id) ter-render
    deleted: bool          # tombstone soft-delete
    metadata: dict[str, Any]


# --------------------------------------------------------------------------- Port

class EmbeddingsPort(Protocol):
    """Jasa embedding. DILARANG fallback beda-model (D6)."""

    @property
    def model(self) -> str: ...
    @property
    def dims(self) -> int: ...
    @property
    def version(self) -> str: ...  # pin ckpt/quant, mis. "int8-onnx-<rev>"

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Satu batch → satu vektor per teks (urutan sama). Gagal = raise
        EmbeddingUnavailableError (D6: JANGAN fallback model lain)."""
        ...


class RerankerPort(Protocol):
    @property
    def model(self) -> str: ...

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> list[RerankedDoc]:
        """Batch WAJIB (dilarang loop sekuensial, D5). `documents` sudah
        dipotong per RAG_*_MAX_TOK oleh pemanggil (HybridRetriever).
        Timeout → raise builtin `TimeoutError` (pemanggil degradasi, D5)."""
        ...


class TokenizerPort(Protocol):
    """Tokenizer model embedding (satuan token = tokenizer BGE-M3, D8)."""

    @property
    def name(self) -> str: ...

    def count(self, text: str) -> int:
        """Jumlah token (prompt-side, sinkron — murah, tanpa I/O)."""
        ...

    def truncate_words(self, text: str, max_tokens: int) -> str:
        """Potong kasar per kata (fallback bila 1 kalimat > batas, D5)."""
        ...


class VectorStorePort(Protocol):
    """Primitif vektor per-backend. Dibangun SUDAH terikat tenant (D2)."""

    backend_name: str

    async def upsert_chunks(
        self, *, doc_id: str, chunks: list[ChunkIn]
    ) -> int:
        """Simpan idempoten (UNIQUE doc_id+ord). Return jumlah tersimpan."""
        ...

    async def search_dense(
        self,
        vector: list[float],
        *,
        k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[CitedChunk]:
        """Top-k ANN. Filter tenant SUDAH di dalam (pre-filter, D2).
        `filters` = metadata BUKAN tenant (doc_type, access_level, ...)."""
        ...

    async def search_lexical(
        self, query: str, *, k: int, filters: dict[str, Any] | None = None
    ) -> list[CitedChunk]:
        """Top-k lexical (tsvector PG / full-text Qdrant). Pre-filter tenant."""
        ...

    async def delete_document(self, *, doc_id: str) -> int:
        """Hapus semua vektor 1 dokumen (milik tenant terikat)."""
        ...

    async def index_info(self) -> IndexInfo:
        """Identitas index — pemanggil validasi cocok dengan RAG_EMBED_* (D4)."""
        ...


class BoundRetriever(Protocol):
    """Retriever terikat tenant — SATU-SATUNYA yang dipakai agen (D2)."""

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        """Hybrid penuh: embed → dense+lexical → RRF → rerank → top-k.
        `filters` = metadata BUKAN tenant (tenant sudah terkunci)."""
        ...


class SourceConnector(Protocol):
    """Konektor sumber read-only (D11). TANPA method tulis — by construction."""

    source_kind: str       # "postgres" | "mysql"

    async def introspect(self) -> list[TableMeta]:
        """Struktur sumber (untuk index metadata). Query introspeksi WAJIB
        read-only + lolos denylist tulis."""
        ...

    async def fetch_incremental(
        self, *, table: str, watermark: str, limit: int
    ) -> tuple[list[SourceRecord], str]:
        """(records baru/berubah, watermark baru). Watermark = (updated_at,id)."""
        ...

    async def fetch_by_id(self, *, table: str, ref: str) -> SourceRecord | None:
        ...


class OCREngine(Protocol):
    """Engine OCR modular (D10). Default: Tesseract ind+eng."""

    engine_name: str

    async def ocr(
        self, pdf_bytes: bytes, *, langs: str, timeout_seconds: int,
        max_pages: int,
    ) -> OCRResult:
        ...


class DocumentParser(Protocol):
    """Parser dokumen modular (D10). Routing by MIME sniff, bukan ekstensi."""

    parser_name: str
    supported_mimes: frozenset[str]

    async def parse(self, blob: bytes, *, mime: str) -> ParsedDocument:
        ...
