"""Centralized settings (12-factor). Route/agent dilarang baca os.environ langsung.

Prinsip ADR-12 di boundary ini: objek Settings yang berhasil dibangun =
konfigurasi VALID. Typo env ditolak (`extra="forbid"`), nilai wajib
divalidasi dengan pesan aksi — jangan lempar ke lapisan infra saat runtime.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    PROJECT_NAME: str = "ai-app-boilerplate"
    API_V1_PREFIX: str = "/api/v1"
    APP_VERSION: str = "v0.8.1"  # satu sumber versi (OpenAPI + health baca ini)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Server (entrypoint `python -m app.main` baca dari sini — .env/OS-env).
    # Dijadikan field resmi agar `extra="forbid"` tak menghukum operator
    # yang menaruh HOST/PORT/RELOAD di .env (temuan F-02).
    HOST: str = "127.0.0.1"  # 0.0.0.0 untuk container/LAN
    PORT: int = Field(default=8000, ge=1, le=65535)
    RELOAD: bool = False  # opt-in true untuk dev

    # CORS: koma-dipisah. Kosong = tolak semua origin browser (paling aman).
    # "*" DITOLAK bila credentials on (kombinasi tak aman/menyesatkan).
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    CORS_ALLOW_METHODS: str = "GET,POST,DELETE,OPTIONS"
    CORS_ALLOW_HEADERS: str = "Content-Type,Authorization,X-Request-ID"

    # Postgres app-db (v0.3+): WAJIB. Tanpa fallback memory — app tolak start
    # bila kosong/tak terjangkau (fail-fast, lihat lifespan di app/main.py).
    DATABASE_URL: str = ""

    # Pool koneksi (satu pool dipakai checkpointer + store bersama).
    DB_POOL_MIN: int = Field(default=2, ge=1)
    DB_POOL_MAX: int = Field(default=10, ge=1)

    # --- v0.4: lingkungan + cache (Valkey/Redis, ADR-13) ---
    ENVIRONMENT: Literal["dev", "staging", "prod"] = "dev"
    # redis://:pass@localhost:6380/0 — kosong = fitur cache NONAKTIF (bukan error).
    CACHE_URL: str = ""
    CACHE_SOCKET_TIMEOUT: float = Field(default=1.5, gt=0, le=30)

    # --- v0.4: auth JWT (ADR-16) ---
    # v0.8.1: mode `trusted_headers` (v0.3, identitas dari header yang bisa
    # dipalsukan) DIHAPUS TOTAL. Template komersial tidak boleh menyediakan
    # jalur auth semacam itu. Satu-satunya mode: JWT Bearer.
    AUTH_MODE: Literal["jwt"] = "jwt"
    JWT_SECRET: str = ""          # wajib di prod, min 32 char (divalidasi)
    JWT_ISSUER: str = "ai-app-boilerplate"
    ACCESS_TOKEN_TTL_SECONDS: int = Field(default=900, ge=60, le=86400)
    REFRESH_TOKEN_TTL_SECONDS: int = Field(default=604800, ge=3600)
    ENABLE_DOCS: bool = False     # /docs + /openapi.json; mati default (prod-safe)

    # --- v0.4: rate limit + idempotency ---
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_CHAT_PER_MIN: int = Field(default=20, ge=1, le=10000)
    RATE_LIMIT_JOBS_PER_MIN: int = Field(default=10, ge=1, le=10000)
    RATE_LIMIT_LOGIN_PER_MIN: int = Field(default=5, ge=1, le=1000)
    IDEMPOTENCY_TTL_SECONDS: int = Field(default=86400, ge=60)
    MAX_JOB_PAYLOAD_BYTES: int = Field(default=262144, ge=1024)

    # LLM — single gateway LiteLLM proxy (v0.2, ADR-01). Tanpa cabang direct.
    # Model/provider diatur di proxy (UI :4000/ui); app hanya tahu nama model + key.
    LITELLM_BASE_URL: str = "http://localhost:4000"
    LITELLM_API_KEY: str = ""  # virtual key per project (BUKAN master key!)
    LITELLM_MODEL: str = ""  # nama model di proxy, mis. "kimi-primary"
    LITELLM_PROJECT_TAG: str = ""  # kosong = pakai PROJECT_NAME (atribusi spend)
    LLM_TEMPERATURE: float = Field(default=1.0, ge=0.0, le=2.0)
    LLM_TIMEOUT_SECONDS: int = Field(default=60, gt=0)

    # Ambang middleware agen (starter UMKM — naikkan per klien bila perlu).
    TOOLCALL_THREAD_LIMIT: int = Field(default=15, ge=1)
    TOOLCALL_PER_TOOL_LIMIT: int = Field(default=5, ge=1)
    CONTEXT_TRIGGER_TOKENS: int = Field(default=8000, ge=1000)
    CONTEXT_KEEP_LAST_USES: int = Field(default=3, ge=1)

    # HITL — selalu false sampai v0.6; slot untuk v0.6.
    HITL_ENABLED: bool = False

    # Durability checkpoint graph: "sync" menulis checkpoint sebelum step
    # berikutnya (wajib untuk alur HITL agar pause tak hilang saat crash),
    # "async" lebih cepat, "exit" hanya di akhir run.
    GRAPH_DURABILITY: Literal["sync", "async", "exit"] = "async"

    # --- v0.5b: WebSocket realtime ---
    WS_ENABLED: bool = True
    WS_TICKET_TTL_SECONDS: int = Field(default=30, ge=5, le=300)
    WS_HEARTBEAT_SECONDS: int = Field(default=25, ge=5, le=120)
    WS_MAX_QUEUE: int = Field(default=256, ge=8, le=10_000)
    # Batas umur koneksi: handshake hanya membuktikan identitas SEKALI, jadi
    # koneksi panjang wajib diputus berkala → klien minta ticket baru lalu
    # reconnect + `resume` (turn log) tanpa kehilangan token jawaban.
    WS_MAX_CONNECTION_SECONDS: int = Field(default=1800, ge=5, le=86_400)
    # Origin yang diizinkan untuk handshake WS (CSWSH). Kosong = pakai CORS_ORIGINS.
    WS_ORIGINS: str = ""

    # Observability Phoenix
    PHOENIX_ENABLED: bool = False
    PHOENIX_ENDPOINT: str = "http://localhost:6006/v1/traces"
    PHOENIX_PROJECT: str = "boilerplate-v0"

    # --- v0.7: RAG (desain beku: Docs/RAG-V07-DESIGN.md) ---
    # Backend vektor: pgvector (default, di app-db) | qdrant (opt-in).
    # Setting DEPLOY, bukan per-request (pindah backend = reindex eksplisit).
    RAG_BACKEND: Literal["pgvector", "qdrant"] = "pgvector"
    # Dedicated infra embedding (compose terpisah, OpenAI-compatible).
    RAG_EMBED_BASE_URL: str = "http://localhost:7997"
    RAG_EMBED_MODEL: str = "BAAI/bge-m3"
    # Pin versi embed (P1-9): kosong = JANGAN validasi versi (perilaku lama);
    # isi = startup tolak bila chunks.embedding_version berbeda (anti campur
    # revisi model/kuantisasi). Samakan dengan versi client embedder.
    RAG_EMBED_VERSION: str = ""
    # Startup ketat (P1-10): true = embed/rerank/vector gagal → tolak start
    # (default, ADR-12); false = RAG degraded (None) + API non-RAG tetap jalan.
    RAG_STRICT_STARTUP: bool = True
    RAG_RERANK_BASE_URL: str = "http://localhost:7997"
    RAG_RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    # Qdrant: wajib keduanya bila RAG_BACKEND=qdrant (fail-fast, D6).
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""
    # Batas ingest (D10): tolak 413 SEBELUM enqueue.
    RAG_MAX_UPLOAD_BYTES: int = Field(default=20_971_520, ge=1024)
    RAG_MAX_PAGES_PDF: int = Field(default=200, ge=1)
    RAG_MAX_XLSX_CELLS: int = Field(default=200_000, ge=100)
    RAG_STORAGE_DIR: str = "data/uploads"
    # Cache HuggingFace lokal repo (tokenizer dsb). Default di repo (BUKAN ~/.cache):
    # reproducible + ramah container (home read-only) + hindari lock-profile.
    RAG_HF_CACHE: str = ".hf-cache"
    # Angka pipeline hybrid (CONFIG, bukan kontrak beku — R7).
    RAG_RRF_K: int = Field(default=60, ge=1)
    RAG_DENSE_TOP: int = Field(default=50, ge=1, le=1000)
    RAG_LEXICAL_TOP: int = Field(default=50, ge=1, le=1000)
    RAG_CANDIDATE_TOP: int = Field(default=20, ge=1, le=200)
    RAG_FINAL_TOP: int = Field(default=5, ge=1, le=50)
    # Truncasi rerank (D5): dipotong di batas kalimat, bukan tengah kalimat.
    RAG_QUERY_MAX_TOK: int = Field(default=128, ge=16)
    RAG_DOC_MAX_TOK: int = Field(default=384, ge=32)
    RAG_PAIR_MAX_TOK: int = Field(default=512, ge=64)
    # Floor skor rerank: semua kandidat di bawah ini → retrieval_status=no_evidence
    # → agen WAJIB menolak tanpa memanggil LLM-answer (D5). 0.0 = nonaktif.
    RAG_RERANK_SCORE_FLOOR: float = Field(default=0.05, ge=0.0, le=1.0)
    # Stemming Sastrawi di normalize.py (v0.7.1): WAJIB sama saat index
    # (worker) dan query (pipeline) — beda = lexical miss sistematis.
    RAG_STEMMER: bool = True
    # Fallback embedding MODEL-IDENTIK (v0.7.1 F5b): OpenAI-compatible
    # `/embeddings` dengan MODEL SAMA (nama + dims divalidasi wiring).
    # Kosong = tanpa fallback (perilaku v0.7: primer mati = 503).
    RAG_EMBED_FALLBACK_URL: str = ""
    RAG_EMBED_FALLBACK_MODEL: str = ""
    RAG_EMBED_TIMEOUT_MS: int = Field(default=1500, ge=100)
    RAG_RERANK_TIMEOUT_MS: int = Field(default=2500, ge=100)
    # RAG_ALLOW_MIXED=1 HANYA untuk re-index bertahap (D6). Default 0 = tolak start
    # bila RAG_EMBED_* tidak cocok dengan kolom/index (anti vektor campur).
    RAG_ALLOW_MIXED: bool = False

    # --- v0.8: Knowledge Graph (desain beku: Docs/RAG-V08-DESIGN.md) ---
    # Default MATI (D14): rollout aman, perilaku v0.7.1 identik bila false.
    GRAPH_ENABLED: bool = False
    # Pin ekstraktor (D6): model+versi; VERSION kosong = jangan validasi versi.
    GRAPH_EXTRACTOR_MODEL: str = "fastino/gliner2.5-multi-v1"
    GRAPH_EXTRACTOR_VERSION: str = ""
    # Batas traversal (beku: 3 hop = tolak start).
    GRAPH_MAX_HOPS: int = Field(default=2, ge=1, le=2)
    GRAPH_SEED_TOP: int = Field(default=8, ge=1, le=32)
    GRAPH_EXPAND_TOP: int = Field(default=20, ge=1, le=200)
    GRAPH_BFS_FANOUT: int = Field(default=50, ge=1, le=500)
    GRAPH_TIMEOUT_MS: int = Field(default=800, ge=100)
    # Batas ekstraksi ingest.
    GRAPH_EXTRACT_BATCH: int = Field(default=8, ge=1, le=64)
    GRAPH_MAX_ENTITIES_PER_CHUNK: int = Field(default=12, ge=1, le=100)
    GRAPH_MAX_EDGES_PER_DOC: int = Field(default=500, ge=1, le=10000)
    GRAPH_MIN_CONFIDENCE: float = Field(default=0.3, ge=0.0, le=1.0)
    GRAPH_SEED_CONFIDENCE: float = Field(default=0.5, ge=0.0, le=1.0)
    # Floor kandidat verbal (D12): nilai awal = warisan; final dari kalibrasi F5.
    GRAPH_SCORE_FLOOR: float = Field(default=0.10, ge=0.0, le=1.0)
    GRAPH_QUOTA_EDGES_PER_WORKSPACE: int = Field(default=500000, ge=1000)
    # L3 (satu-satunya cost center): trigger = L2 menyerah (0 edge ATAU
    # semua edge < 0.5) DAN chunk sensitif (hasil spike F4: 2/12).
    GRAPH_L3_MODEL: str = ""  # kosong = L3 MATI (hemat total; F5 ablation)
    GRAPH_L3_VERSION: str = ""
    GRAPH_L3_MAX_CALLS_PER_DOC: int = Field(default=20, ge=0, le=200)
    GRAPH_L3_MAX_TOKENS_IN: int = Field(default=1500, ge=100)
    GRAPH_L3_MAX_TOKENS_OUT: int = Field(default=500, ge=50)
    GRAPH_L3_DAILY_QUOTA_PER_WORKSPACE: int = Field(default=500, ge=0)
    GRAPH_L3_RETRY_5XX: int = Field(default=1, ge=0, le=3)
    GRAPH_L3_BREAKER_MINUTES: int = Field(default=15, ge=1, le=1440)
    GRAPH_L3_CACHE_TTL_DAYS: int = Field(default=7, ge=1, le=365)
    # Worker ingest (proses terpisah, pool sendiri — T11).
    WORKER_DB_POOL_MIN: int = Field(default=2, ge=1)
    WORKER_DB_POOL_MAX: int = Field(default=5, ge=1)
    WORKER_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    WORKER_POLL_SECONDS: float = Field(default=0.5, gt=0, le=60)
    RAG_LEASE_SECONDS: int = Field(default=120, ge=10, le=3600)
    # Timeout statement (T11): API 30s, worker 120s (bulk insert + HNSW);
    # idle-in-transaction 30s keduanya (bunuh transaksi bocor).
    DB_STATEMENT_TIMEOUT_S: int = Field(default=30, ge=1, le=600)
    DB_IDLE_TXN_TIMEOUT_S: int = Field(default=30, ge=1, le=600)
    WORKER_STATEMENT_TIMEOUT_S: int = Field(default=120, ge=1, le=3600)
    # Kunci Fernet envelope untuk secret konektor (44ch base64). Rotasi manual.
    SOURCE_CONN_KEY: str = ""
    RATE_LIMIT_INGEST_PER_MIN: int = Field(default=5, ge=1, le=1000)
    # Retrieval/search dapat bucket SENDIRI (search cepat & sering; ingest
    # berat & jarang — berbagi bucket = E2E/UX kena 429 palsu).
    RATE_LIMIT_RETRIEVAL_PER_MIN: int = Field(default=30, ge=1, le=1000)
    # OCR (D10): auto = tawarkan OCR_REQUIRED bila teks <50 char/halaman.
    OCR_MODE: Literal["auto", "on", "off"] = "auto"
    OCR_LANGS: str = "ind+eng"
    OCR_TIMEOUT_SECONDS: int = Field(default=60, ge=5, le=600)
    OCR_MAX_PAGES: int = Field(default=50, ge=1)

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _upper_level(cls, v: object) -> object:
        return str(v or "").upper() if isinstance(v, str) else v

    @field_validator("DATABASE_URL")
    @classmethod
    def _strip_database_url(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("LITELLM_API_KEY", "LITELLM_MODEL")
    @classmethod
    def _strip_llm_creds(cls, v: str) -> str:
        # Guard whitespace (temuan F-13): "   " = kosong, bukan model valid.
        return (v or "").strip()

    @field_validator("LITELLM_BASE_URL")
    @classmethod
    def _check_base_url(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("LITELLM_BASE_URL wajib http(s) (dapat "
                             f"{v!r}).")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _check_cors(cls, v: str) -> str:
        origins = [o.strip() for o in (v or "").split(",") if o.strip()]
        if "*" in origins:
            raise ValueError("CORS_ORIGINS='*' tak diizinkan dengan "
                             "allow_credentials=True — sebutkan origin eksplisit.")
        return v

    @field_validator("CACHE_URL")
    @classmethod
    def _check_cache_url(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not v.startswith(("redis://", "rediss://")):
            raise ValueError("CACHE_URL wajib skema redis:// atau rediss:// "
                             f"(dapat {v.split('://')[0]!r}).")
        return v

    @field_validator("JWT_SECRET")
    @classmethod
    def _check_jwt_secret(cls, v: str) -> str:
        v = (v or "").strip()
        if v and len(v) < 32:
            raise ValueError(
                f"JWT_SECRET minimal 32 karakter (dapat {len(v)}). "
                "Generate: [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')")
        return v

    @model_validator(mode="after")
    def _check_pool_order(self):
        if self.DB_POOL_MAX < self.DB_POOL_MIN:
            raise ValueError(
                f"DB_POOL_MAX ({self.DB_POOL_MAX}) < DB_POOL_MIN "
                f"({self.DB_POOL_MIN}) — perbaiki env.")
        return self

    @model_validator(mode="after")
    def _check_v04_guards(self):
        """Guard konsistensi mode auth + larangan kombinasi tak aman di prod."""
        if self.ENVIRONMENT == "prod":
            if self.ENABLE_DOCS:
                raise ValueError("ENVIRONMENT=prod mewajibkan ENABLE_DOCS=false.")
            if not self.CACHE_URL:
                raise ValueError(
                    "ENVIRONMENT=prod mewajibkan CACHE_URL (rate-limit/idempotency).")
            if not self.JWT_SECRET:
                raise ValueError("ENVIRONMENT=prod mewajibkan JWT_SECRET.")
        return self

    @field_validator("RAG_EMBED_BASE_URL", "RAG_RERANK_BASE_URL", "QDRANT_URL")
    @classmethod
    def _check_rag_urls(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"URL RAG wajib http(s) (dapat {v!r}).")
        return v

    @field_validator("SOURCE_CONN_KEY")
    @classmethod
    def _check_source_key(cls, v: str) -> str:
        """Kunci Fernet envelope: 44 char base64 urlsafe (Fernet.generate_key)."""
        import re

        v = (v or "").strip()
        if v and not re.fullmatch(r"[A-Za-z0-9_-]{43}=", v):
            raise ValueError(
                "SOURCE_CONN_KEY wajib kunci Fernet 44 char "
                "(dapat panjang berbeda/format salah). "
                "Generate: python -c 'from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())'")
        return v

    @model_validator(mode="after")
    def _check_v07_guards(self):
        """Guard konsistensi RAG (D14): backend qdrant tanpa URL/key = error
        config eksplisit; FINAL_TOP tak boleh melebihi CANDIDATE_TOP."""
        if self.RAG_BACKEND == "qdrant" and not self.QDRANT_URL:
            raise ValueError(
                "RAG_BACKEND=qdrant mewajibkan QDRANT_URL. Isi URL + "
                "QDRANT_API_KEY, atau kembalikan RAG_BACKEND=pgvector.")
        if self.RAG_BACKEND == "qdrant" and not self.QDRANT_API_KEY:
            raise ValueError(
                "RAG_BACKEND=qdrant mewajibkan QDRANT_API_KEY (auth Qdrant "
                "tanpa key = 401; dashboard tak boleh publik).")
        if self.WORKER_DB_POOL_MAX < self.WORKER_DB_POOL_MIN:
            raise ValueError(
                f"WORKER_DB_POOL_MAX ({self.WORKER_DB_POOL_MAX}) < "
                f"WORKER_DB_POOL_MIN ({self.WORKER_DB_POOL_MIN}) — perbaiki env.")
        if self.RAG_FINAL_TOP > self.RAG_CANDIDATE_TOP:
            raise ValueError(
                f"RAG_FINAL_TOP ({self.RAG_FINAL_TOP}) > RAG_CANDIDATE_TOP "
                f"({self.RAG_CANDIDATE_TOP}) — final tak bisa melebihi kandidat.")
        return self

    @model_validator(mode="after")
    def _check_v08_guards(self):
        """Guard konsistensi KG (D4/D6): enabled wajib model; expand_top tak
        boleh melebihi candidate_top; max_hops ≤ 2 (3 = tolak start)."""
        if self.GRAPH_ENABLED and not (self.GRAPH_EXTRACTOR_MODEL or "").strip():
            raise ValueError(
                "GRAPH_ENABLED=true mewajibkan GRAPH_EXTRACTOR_MODEL non-kosong "
                "(pin model ekstraktor, D6).")
        if self.GRAPH_EXPAND_TOP > self.RAG_CANDIDATE_TOP:
            raise ValueError(
                f"GRAPH_EXPAND_TOP ({self.GRAPH_EXPAND_TOP}) > RAG_CANDIDATE_TOP "
                f"({self.RAG_CANDIDATE_TOP}) — kandidat graph tak bisa melebihi pool.")
        if self.GRAPH_MAX_HOPS > 2:
            raise ValueError(
                f"GRAPH_MAX_HOPS ({self.GRAPH_MAX_HOPS}) > 2 — v0.8 bounded 2-hop "
                "(D3). Naikkan hanya bila desain direvisi + eval ulang.")
        return self

    @property
    def auth_configured(self) -> bool:
        """True bila mode auth aktif benar-benar bisa melayani request."""
        return bool(self.JWT_SECRET)

    @property
    def cache_enabled(self) -> bool:
        return bool(self.CACHE_URL)

    @property
    def rag_enabled(self) -> bool:
        """True bila jalur RAG bisa dipakai (embed terkonfigurasi)."""
        return bool(self.RAG_EMBED_BASE_URL and self.RAG_EMBED_MODEL)

    @property
    def qdrant_configured(self) -> bool:
        return bool(self.QDRANT_URL and self.QDRANT_API_KEY)

    def cors_origins(self) -> list[str]:
        """Parse koma-dipisah (milik config, bukan factory app — SRP)."""
        return [o.strip() for o in (self.CORS_ORIGINS or "").split(",")
                if o.strip()]

    def cors_methods(self) -> list[str]:
        return [m.strip().upper() for m in (self.CORS_ALLOW_METHODS or "").split(",")
                if m.strip()]

    def cors_headers(self) -> list[str]:
        return [h.strip() for h in (self.CORS_ALLOW_HEADERS or "").split(",")
                if h.strip()]

    def ws_origins(self) -> list[str]:
        """Origin yang diizinkan untuk WS; fallback ke CORS_ORIGINS."""
        raw = (self.WS_ORIGINS or "").strip() or self.CORS_ORIGINS
        return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cache proses. Test ber-env beda: `get_settings.cache_clear()` dulu.

    Contoh fixture:
        @pytest.fixture
        def _env(monkeypatch):
            get_settings.cache_clear()
            monkeypatch.setenv("DATABASE_URL", "...")
            yield
            get_settings.cache_clear()
    """
    return Settings()


