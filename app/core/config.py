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
    APP_VERSION: str = "v0.3.1"  # satu sumber versi (OpenAPI + health baca ini)
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
    CORS_ALLOW_HEADERS: str = ("Content-Type,Authorization,X-Request-ID,"
                               "X-Tenant-ID,X-User-ID")

    # Postgres app-db (v0.3+): WAJIB. Tanpa fallback memory — app tolak start
    # bila kosong/tak terjangkau (fail-fast, lihat lifespan di app/main.py).
    DATABASE_URL: str = ""

    # Pool koneksi (satu pool dipakai checkpointer + store bersama).
    DB_POOL_MIN: int = Field(default=2, ge=1)
    DB_POOL_MAX: int = Field(default=10, ge=1)

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

    # Observability Phoenix
    PHOENIX_ENABLED: bool = False
    PHOENIX_ENDPOINT: str = "http://localhost:6006/v1/traces"
    PHOENIX_PROJECT: str = "boilerplate-v0"

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

    @model_validator(mode="after")
    def _check_pool_order(self):
        if self.DB_POOL_MAX < self.DB_POOL_MIN:
            raise ValueError(
                f"DB_POOL_MAX ({self.DB_POOL_MAX}) < DB_POOL_MIN "
                f"({self.DB_POOL_MIN}) — perbaiki env.")
        return self

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
