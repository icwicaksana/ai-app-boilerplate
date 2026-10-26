"""Unit infra berisiko (temuan F-07): postgres kwargs, llm factory, phoenix no-op.

Tanpa DB/network — mock konstruktor + jalur no-op.
"""

from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.infra import postgres


def _settings(**kw):
    args = {"_env_file": None, "DATABASE_URL": "postgresql://u:p@h:5432/db"}
    args.update(kw)
    return Settings(**args)


def test_normalize_conninfo_prefix_only():
    assert postgres.normalize_conninfo(
        "postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
    # Password mengandung substring skema → JANGAN ditulis ulang.
    tricky = "postgresql://u:postgresql+psycopg://x@h/db"
    assert postgres.normalize_conninfo(tricky) == tricky
    assert postgres.normalize_conninfo("  ") == ""


def test_create_pool_kwargs_and_open_false():
    from psycopg.rows import dict_row

    pool = postgres.create_pool(_settings(DB_POOL_MIN=2, DB_POOL_MAX=7))
    try:
        # open=False: pool TERTUTUP saat dibuat (lifespan yang open/close).
        assert pool.kwargs.get("autocommit") is True
        assert pool.kwargs.get("prepare_threshold") == 0
        # row_factory dict_row — gagal di sini = setup() gagal CONCURRENTLY.
        assert pool.kwargs.get("row_factory") is dict_row
    finally:
        pass  # pool tak pernah open → tak perlu close.


def test_create_pool_rejects_empty_url():
    with pytest.raises(RuntimeError):
        postgres.create_pool(_settings(DATABASE_URL=""))


def test_llm_factory_single_gateway_contract():
    """max_retries=0, custom provider openai, spend via extra_body, header sesi."""
    from app.infra import llm as llm_mod

    with patch.object(llm_mod, "ChatLiteLLM") as mock:
        llm_mod.get_llm(_settings(LITELLM_API_KEY="vk-x",
                                  LITELLM_MODEL="k-primary",
                                  LITELLM_PROJECT_TAG="toko-a"), "t:a:s")
        _, kwargs = mock.call_args
    assert kwargs["model"] == "k-primary"
    assert kwargs["custom_llm_provider"] == "openai"
    assert kwargs["max_retries"] == 0
    body = kwargs["model_kwargs"]["extra_body"]
    assert body["metadata"]["spend_logs_metadata"]["project_id"] == "toko-a"
    assert body["metadata"]["spend_logs_metadata"]["thread_id"] == "t:a:s"
    assert kwargs["extra_headers"]["x-opencode-session"] == "t:a:s"


def test_llm_factory_rejects_empty_creds():
    from app.core.errors import UpstreamAIError
    from app.infra.llm import get_llm

    with pytest.raises(UpstreamAIError):
        get_llm(_settings(LITELLM_API_KEY="", LITELLM_MODEL="m"))
    with pytest.raises(UpstreamAIError):
        get_llm(_settings(LITELLM_API_KEY="k", LITELLM_MODEL=""))


def test_phoenix_disabled_is_noop():
    from app.infra.phoenix import setup_phoenix_tracing

    assert setup_phoenix_tracing(_settings(PHOENIX_ENABLED=False)) is False


def test_setup_logging_rejects_bad_level():
    from app.core.logging import setup_logging

    with pytest.raises(ValueError):
        setup_logging("VERBOSE")
