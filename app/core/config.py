"""Centralized settings (12-factor). Route/agent dilarang baca os.environ langsung."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "ai-app-boilerplate"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # CORS: koma-dipisah. Kosong = tolak semua origin browser (paling aman).
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Postgres app-db (v0.3+): WAJIB. Tanpa fallback memory — app tolak start
    # bila kosong/tak terjangkau (fail-fast, lihat lifespan di app/main.py).
    DATABASE_URL: str = ""

    # Pool koneksi (satu pool dipakai checkpointer + store bersama).
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 10

    # LLM — single gateway LiteLLM proxy (v0.2, ADR-01). Tanpa cabang direct.
    # Model/provider diatur di proxy (UI :4000/ui); app hanya tahu nama model + key.
    LITELLM_BASE_URL: str = "http://localhost:4000"
    LITELLM_API_KEY: str = ""  # virtual key per project (BUKAN master key!)
    LITELLM_MODEL: str = ""  # nama model di proxy, mis. "kimi-primary"
    LITELLM_PROJECT_TAG: str = ""  # kosong = pakai PROJECT_NAME (atribusi spend)
    LLM_TEMPERATURE: float = 1.0
    LLM_TIMEOUT_SECONDS: int = 60

    # HITL — v0 selalu false, slot untuk v1
    HITL_ENABLED: bool = False

    # Observability Phoenix
    PHOENIX_ENABLED: bool = False
    PHOENIX_ENDPOINT: str = "http://localhost:6006/v1/traces"
    PHOENIX_PROJECT: str = "boilerplate-v0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
