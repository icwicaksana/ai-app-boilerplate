"""Bersama integration test v0.3: DB live, TANPA LLM.

- Butuh `app-db` + `TEST_DATABASE_URL` (fallback `DATABASE_URL`).
  Tak terjangkau → SKIP (bukan fail) — agar mesin tanpa docker tetap hijau.
- Windows: selector event-loop policy WAJIB (psycopg menolak Proactor).
- Tiap test: tenant unik `it-<hex>` + cleanup best-effort → idempotent.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Muat `.env` repo secara eksplisit: test integrasi WAJIB memakai config nyata
# (DATABASE_URL/CACHE_URL), dan perilaku `load_dotenv` pihak ketiga tidak boleh
# jadi ketergantungan tersembunyi. `override=False` = env OS tetap menang.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

from app.core.config import Settings  # noqa: E402
from app.infra.postgres import (  # noqa: E402
    close_pool,
    create_pool,
    get_checkpointer,
    get_store,
    normalize_conninfo,
    open_pool,
    setup_persistence,
)


def require_db() -> str:
    """URL DB live, atau SKIP bila tak terjangkau (connect_timeout 2s)."""
    raw = (os.environ.get("TEST_DATABASE_URL")
           or os.environ.get("DATABASE_URL", "")).strip()
    if not raw:
        pytest.skip("TEST_DATABASE_URL/DATABASE_URL kosong — nyalakan app-db dulu")
    url = normalize_conninfo(raw)
    try:
        import psycopg
        conn = psycopg.connect(url, connect_timeout=2)
        conn.close()
    except Exception as exc:
        pytest.skip(f"app-db tak terjangkau ({type(exc).__name__}) — nyalakan dulu")
    return raw


@pytest.fixture
def db_url() -> str:
    return require_db()


@pytest.fixture
def tag() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def persistence(db_url):
    """(pool, checkpointer, store) segar per test + setup + close.

    Scope fungsi = tiap test menyerupai worker/restart baru (realistis).
    """
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=2, DB_POOL_MAX=10)
    pool = create_pool(settings)
    await open_pool(pool)
    checkpointer = get_checkpointer(pool)
    store = get_store(pool)
    await setup_persistence(checkpointer, store)
    yield pool, checkpointer, store
    await close_pool(pool)


# --- v0.4: cache (Valkey) live ---

def require_cache() -> str:
    """URL cache live, atau SKIP bila tak terjangkau."""
    raw = (os.environ.get("TEST_CACHE_URL")
           or os.environ.get("CACHE_URL", "")).strip()
    if not raw:
        pytest.skip("TEST_CACHE_URL/CACHE_URL kosong — nyalakan cache dulu")
    return raw


@pytest_asyncio.fixture
async def cache_url() -> str:
    return require_cache()


@pytest_asyncio.fixture
async def cache(cache_url):
    """CacheClient nyata (Valkey). Skip bila ping gagal."""
    from app.infra.cache import CacheClient

    client = CacheClient.from_url(cache_url, socket_timeout=2.0)
    if not await client.ping():
        await client.close()
        pytest.skip("Valkey tak terjangkau — `docker compose up -d cache`")
    yield client
    await client.close()


@pytest_asyncio.fixture
async def pool(db_url):
    """Pool Postgres mentah (untuk repo user di test auth)."""
    from app.infra.postgres import create_pool as _create

    p = _create(Settings(_env_file=None, DATABASE_URL=db_url))
    await open_pool(p)
    yield p
    await close_pool(p)


# --- v0.7: embedding service (Infinity) live ---

def require_embed() -> str:
    """Base URL service embedding, atau SKIP bila tak terjangkau."""
    raw = (os.environ.get("TEST_EMBED_BASE_URL")
           or os.environ.get("RAG_EMBED_BASE_URL",
                             "http://localhost:7997")).strip()
    try:
        import urllib.request
        urllib.request.urlopen(raw.rstrip("/") + "/health", timeout=3)
    except Exception:
        pytest.skip(f"service embedding tak terjangkau ({raw}) — "
                    "`docker compose -f docker-compose.embed.yml up -d`")
    return raw


@pytest_asyncio.fixture
async def embed_url() -> str:
    return require_embed()
