"""Validasi boundary config (temuan core #4/#5/#16, F-13/F-14).

Prinsip: Settings yang berhasil dibangun = konfigurasi VALID. Typo env
ditolak, nilai wajib lolos batas dengan pesan aksi.
"""

import pytest

from app.core.config import Settings, get_settings


def _base(**kw):
    args = {"_env_file": None}
    args.update(kw)
    return Settings(**args)


def test_extra_forbid_rejects_typo():
    with pytest.raises(Exception):
        _base(DATABSE_URL="x")  # typo → jangan diam-diam diabaikan


def test_pool_order_validated():
    with pytest.raises(Exception):
        _base(DATABASE_URL="postgresql://x", DB_POOL_MIN=10, DB_POOL_MAX=2)


def test_pool_fields_positive():
    with pytest.raises(Exception):
        _base(DATABASE_URL="postgresql://x", DB_POOL_MIN=0)
    s = _base(DATABASE_URL="postgresql://x", DB_POOL_MIN=1, DB_POOL_MAX=1)
    assert (s.DB_POOL_MIN, s.DB_POOL_MAX) == (1, 1)


def test_llm_creds_whitespace_treated_empty():
    s = _base(LITELLM_MODEL="   ", LITELLM_API_KEY="  ")
    assert s.LITELLM_MODEL == "" and s.LITELLM_API_KEY == ""


def test_base_url_scheme_validated():
    with pytest.raises(Exception):
        _base(LITELLM_BASE_URL="ftp://x")
    assert _base(LITELLM_BASE_URL="http://h:4000/").LITELLM_BASE_URL == \
        "http://h:4000"


def test_log_level_literal():
    with pytest.raises(Exception):
        _base(LOG_LEVEL="DEBGU")
    assert _base(LOG_LEVEL="debug").LOG_LEVEL == "DEBUG"


def test_temperature_and_timeout_bounds():
    with pytest.raises(Exception):
        _base(LLM_TEMPERATURE=3.0)
    with pytest.raises(Exception):
        _base(LLM_TIMEOUT_SECONDS=0)


def test_cors_wildcard_rejected():
    with pytest.raises(Exception):
        _base(CORS_ORIGINS="*")


def test_cors_helpers_parse():
    s = _base(CORS_ORIGINS="http://a, http://b ",
              CORS_ALLOW_METHODS="get,post",
              CORS_ALLOW_HEADERS="X-A, X-B")
    assert s.cors_origins() == ["http://a", "http://b"]
    assert s.cors_methods() == ["GET", "POST"]
    assert s.cors_headers() == ["X-A", "X-B"]


def test_app_version_single_source():
    assert _base().APP_VERSION.startswith("v")


def test_get_settings_cache_clear_pattern():
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_graph_guard_enabled_wajibkan_model():
    with pytest.raises(Exception, match="GRAPH_EXTRACTOR_MODEL"):
        _base(GRAPH_ENABLED=True, GRAPH_EXTRACTOR_MODEL="  ")
    s = _base(GRAPH_ENABLED=True)
    assert s.GRAPH_EXTRACTOR_MODEL == "fastino/gliner2.5-multi-v1"


def test_graph_guard_expand_top_dan_hops():
    with pytest.raises(Exception, match="GRAPH_EXPAND_TOP"):
        _base(RAG_CANDIDATE_TOP=20, GRAPH_EXPAND_TOP=21)
    with pytest.raises(Exception, match="GRAPH_MAX_HOPS|less than or equal"):
        _base(GRAPH_MAX_HOPS=3)
    s = _base()
    assert (s.GRAPH_ENABLED, s.GRAPH_MAX_HOPS) is not None
    assert s.GRAPH_ENABLED is False and s.GRAPH_MAX_HOPS == 2
