"""Port RAG v0.7 (KONTRAK BEKU — desain: Docs/RAG-V07-DESIGN.md D1).

Aturan modul (test arsitektur `test_rag_arch.py`):
- File ini + seluruh `app/rag/` DILARANG mengimpor `app/infra`, `httpx`,
  `qdrant_client`, `psycopg`, `fastapi`, atau SDK model. Pure Python + pydantic.
- Workspace dikunci saat CONSTRUCTION (factory-bound, D2) — tidak ada argumen
  workspace di method query. Implementasi yang menerima workspace per-call = langgar kontrak.

Lapis:
- `EmbeddingsPort` / `RerankerPort` — jasa model (adapter: Infinity).
- `VectorStorePort` — primitif per-backend (adapter: pgvector / Qdrant).
  `RAG_BACKEND` mengganti INI, bukan seluruh retrieval.
- `BoundRetriever` — hasil `RetrieverFactory.for_workspace()`; dipakai agen.
- `SourceConnector` / `OCREngine` / `DocumentParser` — ingest (D10/D11).
- v0.8 (Docs/RAG-V08-DESIGN.md): `GraphStore` + `EntityExtractor` +
  `GraphRanker` — graph PG-native doc-scoped, factory-bound (D2).
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
    kind: NotRequired[Literal["vector", "graph"]]  # v0.8 D8; absent = "vector"
    provenance: NotRequired[list[str]]  # v0.8 D8: chunk_id per hop (graph saja)


class ChunkIn(TypedDict, total=False):
    """Chunk siap index (datang dari worker ingest)."""

    content: str           # required (total=False + validasi di adapter)
    content_norm: str      # lexical ternormalisasi (v0.7.1; None = mentah)
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


class RetrievalTrace(TypedDict):
    """Jejak 1x retrieval: timing + hitungan + skor ringkas (v0.7.1 F1).

    Tujuan: diagnosis "kenapa jawaban buruk" tanpa menebak — eval F4 dan
    operator membaca ini, bukan log tersebar. Murah (perf_counter + len),
    selalu dikumpulkan; konsumen memilih ekspos penuh (debug) atau ringkas.
    """

    backend: str           # backend_name store
    timings_ms: dict[str, float]  # embed/dense/lexical/rerank/total
    counts: dict[str, int]  # dense/lexical/candidates/ranked/final
    top_score: float       # skor final tertinggi (rerank; RRF bila degraded)
    floor: float           # score_floor aktif (= natural; kompat eval lama)
    status: str            # duplikat status hasil (korelasi log)
    degraded: list[str]    # duplikat flag degraded
    query_preview: str     # ≤120 char (korelasi, bukan isi penuh)
    # v0.8 D11 (aditif; absent = graph nonaktif):
    graph_timings_ms: NotRequired[dict[str, float]]  # seed/expand/verbalize
    graph_counts: NotRequired[dict[str, int]]  # seeds/paths/kept/cap/fanout_clipped
    seed_confidence: NotRequired[float | None]
    floors: NotRequired[dict[str, float]]  # {"natural":…, "graph":…}


class RetrievalResult(TypedDict):
    chunks: list[CitedChunk]
    status: Literal["ok", "no_evidence"]
    degraded: list[str]    # mis. ["rerank_timeout"] — aditif
    trace: NotRequired[RetrievalTrace]  # observability (v0.7.1, aditif)


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
    """Primitif vektor per-backend. Dibangun SUDAH terikat workspace (D2)."""

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
        """Top-k ANN. Filter workspace SUDAH di dalam (pre-filter, D2).
        `filters` = metadata BUKAN workspace (doc_type, access_level, ...)."""
        ...

    async def search_lexical(
        self, query: str, *, k: int, filters: dict[str, Any] | None = None,
        raw_query: str | None = None,
    ) -> list[CitedChunk]:
        """Top-k lexical (tsvector PG / full-text Qdrant). Pre-filter workspace.

        `query` = SUDAH dinormalisasi pipeline (normalize.py); `raw_query` =
        query mentah untuk cabang fallback baris lama (pre-010, tanpa
        `content_norm`) — cocok NORMA ATAU MENTAH, tanpa backfill.
        `raw_query=None` = panggil langsung (test kontrak): pakai `query`
        untuk kedua cabang (perilaku warisan).
        """
        ...

    async def delete_document(self, *, doc_id: str) -> int:
        """Hapus semua vektor 1 dokumen (milik workspace terikat)."""
        ...

    async def index_info(self) -> IndexInfo:
        """Identitas index — pemanggil validasi cocok dengan RAG_EMBED_* (D4)."""
        ...


class BoundRetriever(Protocol):
    """Retriever terikat workspace — SATU-SATUNYA yang dipakai agen (D2)."""

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        """Hybrid penuh: embed → dense+lexical → RRF → rerank → top-k.
        `filters` = metadata BUKAN workspace (workspace sudah terkunci)."""
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


class KgEntityIn(TypedDict, total=False):
    """Entity siap tulis (datang dari worker graph). Doc-scoped (D2)."""

    name: str            # tampil ("PT Maju Jaya")
    norm: str            # kunci dedup (lower+fold TANPA stem)
    type: str            # tipe ontologi core / workspace:*
    confidence: float    # 0..1
    chunk_ord: int       # provenance → chunk
    chunk_id: str        # format citations.chunk_id (stabil)


class KgEdgeIn(TypedDict, total=False):
    """Edge siap tulis. Semua kolom denormalisasi anti-JOIN (D13/G1)."""

    src_norm: str
    dst_norm: str
    relation: str        # relasi ontologi core / workspace:* / related_to
    confidence: float
    layer: str           # "L1" | "L2" | "L3"
    coref_resolved: bool
    access_level: str    # denormalisasi dari chunk/doc (D13)
    chunk_ord: int
    chunk_id: str
    chunk_row_id: str    # UUID chunks.id (G1 tanpa Seq Scan)


class KgHit(TypedDict):
    """Satu evidence graph → pseudo-chunk (D8)."""

    chunk_id: str        # gpath stabil (sha256 sorted edge_ids + preset)
    doc_id: str          # doc hop-1 (primer)
    content: str         # 1–2 kalimat verbal
    source: str          # nama file hop-1
    page: int | None
    score: float         # ordinal pre-RRF (akan di-RRF ulang)
    hops: int            # 1 | 2
    metadata: dict[str, Any]  # kind/provenance/confidence/layers/extractor


class KgPath(TypedDict):
    """Jalur BFS mentah untuk GraphRanker (slot skala)."""

    nodes: list[str]     # norm per node
    edges: list[int]     # id edge per hop
    score: float


class GraphInfo(TypedDict):
    """Identitas graph — pemanggil validasi cocok pin ekstraktor (D4/D6)."""

    extractor: str
    extractor_version: str
    onto_version: str
    entity_count: int | None
    edge_count: int | None


class Extraction(TypedDict):
    """Hasil ekstraksi 1 teks (EntityExtractor batch)."""

    entities: list[KgEntityIn]
    edges: list[KgEdgeIn]


class GraphStore(Protocol):
    """Store graph factory-bound workspace (D2) — tanpa argumen workspace."""

    backend_name: str    # "pggraph"

    async def upsert_subgraph(
        self, *, doc_id: str,
        entities: list[KgEntityIn], edges: list[KgEdgeIn],
    ) -> tuple[int, int]:
        """Tulis idempoten 1 dokumen (ON CONFLICT). Return (n_ent, n_edge)."""
        ...

    async def expand(
        self, seeds: list[str], *, k: int,
        filters: dict[str, Any] | None = None,
        max_hops: int = 2, fanout: int = 50,
    ) -> list[KgHit]:
        """Seed (norm entitas query) → BFS bounded → evidence. Pre-filter
        workspace + whitelist filter SAMA dengan VectorStore di tiap hop (D13)."""
        ...

    async def resolve_seeds(self, seeds: list[str]) -> list[str]:
        """Norm seed → norm kanonis (alias workspace + keberadaan entity).

        Return subset yang MATCH entity (sesudah alias). Pipeline memakai
        rasio match/request sebagai `seed_confidence` REAL (F4).
        """
        ...

    async def upsert_aliases(self, aliases: dict[str, str]) -> int:
        """Tulis alias workspace (alias_norm → canonical_norm). Return jumlah."""
        ...

    async def list_aliases(self) -> dict[str, str]:
        """Seluruh alias workspace (tabel kecil; pipeline cache per-request).

        Dipakai seed preseed: substring-match query (longest-first) agar
        alias multi-kata ("komputer jinjing", "ess") selalu terjangkau
        tanpa tergantung cutoff seed_top (pelajaran F5 run 4).
        """
        ...

    async def delete_subgraph(self, *, doc_id: str) -> tuple[int, int]:
        """Hapus entitas+edge 1 dokumen (milik workspace terikat). Idempoten."""
        ...

    async def graph_info(self) -> GraphInfo:
        """Identitas graph — pemanggil validasi cocok pin ekstraktor."""
        ...


class EntityExtractor(Protocol):
    """Jasa model ekstraksi (D6: dilarang fallback beda-model)."""

    @property
    def model(self) -> str:
        ...

    @property
    def version(self) -> str:
        ...

    async def extract(self, texts: list[str]) -> list[Extraction]:
        """Batch WAJIB (dilarang loop sekuensial). Gagal = raise
        TimeoutError/ExtractorError; pemanggil degradasi, bukan retry buta."""
        ...


class GraphRanker(Protocol):
    """Slot ranking path (v0.8: BFSRanker; PPR nanti — tier-M)."""

    async def rank(self, paths: list[KgPath], query: str) -> list[KgPath]:
        """Urutkan path terbaik dulu (deterministik, pure function)."""
        ...
