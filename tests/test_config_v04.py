"""Unit: validasi boundary config v0.4 (cache, auth, hardening).

Prinsip sama seperti v0.3.1: Settings yang berhasil dibangun = konfigurasi
VALID; kombinasi tidak aman ditolak dengan pesan aksi.

CATATAN penting: rantai import `langchain`/`langsmith` memanggil
`load_dotenv()` saat import, sehingga isi `.env` BOCOR ke `os.environ`.
Akibatnya test yang membangun `Settings(_env_file=None, ...)` tetap
mewarisi nilai `.env` — karena itu test di bawah SELALU menyebut eksplisit
field yang relevan dengan kombinasi yang diuji.
"""

import pytest

from app.core.config import Settings

SECRET = "x" * 40
CACHE = "redis://:p@localhost:6380/0"
PROD_OK = {"ENVIRONMENT": "prod", "AUTH_MODE": "jwt", "JWT_SECRET": SECRET,
           "CACHE_URL": CACHE, "ENABLE_DOCS": False}


def _s(**kw) -> Settings:
    args = {"_env_file": None}
    args.update(kw)
    return Settings(**args)


# --- cache ---

def test_cache_url_scheme_validated():
    with pytest.raises(Exception):
        _s(CACHE_URL="http://localhost:6380")
    assert _s(CACHE_URL="redis://:p@h:6380/0").CACHE_URL == "redis://:p@h:6380/0"
    assert _s(CACHE_URL="rediss://h:6380/0").CACHE_URL.startswith("rediss://")


def test_cache_url_stripped():
    assert _s(CACHE_URL="  redis://h  ").CACHE_URL == "redis://h"


def test_cache_enabled_property():
    assert _s(CACHE_URL="").cache_enabled is False
    assert _s(CACHE_URL=CACHE).cache_enabled is True


def test_cache_socket_timeout_bounds():
    with pytest.raises(Exception):
        _s(CACHE_SOCKET_TIMEOUT=0)
    with pytest.raises(Exception):
        _s(CACHE_SOCKET_TIMEOUT=999)


# --- auth ---

def test_jwt_secret_min_length_when_set():
    with pytest.raises(Exception):
        _s(JWT_SECRET="pendek")
    assert _s(JWT_SECRET=SECRET).JWT_SECRET == SECRET


def test_auth_configured_property():
    assert _s(AUTH_MODE="jwt", JWT_SECRET="").auth_configured is False
    assert _s(AUTH_MODE="jwt", JWT_SECRET=SECRET).auth_configured is True
    assert _s(AUTH_MODE="trusted_headers",
              AUTH_ALLOW_TRUSTED_HEADERS=True).auth_configured is True


def test_trusted_headers_requires_explicit_flag():
    with pytest.raises(Exception):
        _s(AUTH_MODE="trusted_headers", AUTH_ALLOW_TRUSTED_HEADERS=False)


def test_ttl_bounds():
    with pytest.raises(Exception):
        _s(ACCESS_TOKEN_TTL_SECONDS=1)
    with pytest.raises(Exception):
        _s(ACCESS_TOKEN_TTL_SECONDS=10**9)


def test_environment_literal():
    with pytest.raises(Exception):
        _s(ENVIRONMENT="production")
    assert _s(**PROD_OK).ENVIRONMENT == "prod"


# --- guard prod: kombinasi tidak aman ditolak ---

def test_prod_requires_jwt_mode():
    with pytest.raises(Exception):
        _s(**{**PROD_OK, "AUTH_MODE": "trusted_headers",
              "AUTH_ALLOW_TRUSTED_HEADERS": True})


def test_prod_forbids_trusted_headers_flag():
    with pytest.raises(Exception):
        _s(**{**PROD_OK, "AUTH_ALLOW_TRUSTED_HEADERS": True})


def test_prod_requires_docs_disabled():
    with pytest.raises(Exception):
        _s(**{**PROD_OK, "ENABLE_DOCS": True})


def test_prod_requires_cache():
    with pytest.raises(Exception):
        _s(**{**PROD_OK, "CACHE_URL": ""})


def test_prod_requires_jwt_secret():
    with pytest.raises(Exception):
        _s(**{**PROD_OK, "JWT_SECRET": ""})


def test_prod_valid_combination_accepted():
    s = _s(**PROD_OK)
    assert s.ENVIRONMENT == "prod" and s.auth_configured is True


# --- rate limit + payload cap ---

def test_rate_limit_bounds():
    with pytest.raises(Exception):
        _s(RATE_LIMIT_CHAT_PER_MIN=0)
    with pytest.raises(Exception):
        _s(RATE_LIMIT_LOGIN_PER_MIN=0)


def test_idempotency_ttl_and_payload_cap_bounds():
    with pytest.raises(Exception):
        _s(IDEMPOTENCY_TTL_SECONDS=10)
    with pytest.raises(Exception):
        _s(MAX_JOB_PAYLOAD_BYTES=10)


def test_extra_forbid_still_applies_to_v04_keys():
    with pytest.raises(Exception):
        _s(JWT_SECRT=SECRET)  # typo → ditolak, bukan diabaikan
