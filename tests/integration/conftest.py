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

import pytest
import pytest_asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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
