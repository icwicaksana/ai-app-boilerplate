"""Centralized settings (12-factor). Route/agent dilarang baca os.environ langsung."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "ai-boilerplate-v0"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # Postgres — kosong = memory checkpointer (dev cepat tanpa docker)
    DATABASE_URL: str = ""

    # LLM
    OPENAI_API_KEY: str = ""
    MODEL_PROVIDER: str = "openai"
    MODEL_NAME: str = "gpt-4o-mini"
    # OpenCode Go — base_url tanpa /chat/completions (ditambah otomatis oleh client)
    OPENCODE_API_KEY: str = ""
    OPENCODE_MODEL: str = "kimi-k2.7-code"
    OPENCODE_BASE_URL: str = "https://opencode.ai/zen/go/v1"
    LLM_TEMPERATURE: float = 0.0
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
