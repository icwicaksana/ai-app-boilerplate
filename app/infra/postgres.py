"""Persistence Postgres app-db (v0.3+): 1 pool dipakai checkpointer + store bersama.

- Pool: `psycopg_pool.AsyncConnectionPool` (min/max dari settings). Dibuka sekali
  di lifespan startup, ditutup saat shutdown. 1 pool per worker uvicorn.
- Checkpointer: `AsyncPostgresSaver(pool)` — snapshot state per thread.
- Store: `AsyncPostgresStore(pool)` — mentah; dibungkus `MemoryStore` per-request
  (lihat `app/infra/memory.py`) agar namespace tenant/user terkunci.
- kwargs koneksi MENIRU `from_conn_string` resmi (`autocommit`,
  `prepare_threshold=0`, `dict_row`) — tanpanya `setup()` gagal dengan
  `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`.
- v0.3+: TANPA fallback memory. `DATABASE_URL` kosong/tak terjangkau = app tolak
  start (fail-fast di lifespan). Test yang butuh DB di-skip bila DB mati.

Controller/agent tidak boleh tahu detail ini — mereka terima objek jadi via
`app.core.dependencies` (yang membaca `app.state`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

if TYPE_CHECKING:  # impor berat hanya untuk type-hint; runtime diimpor lazy
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore


def normalize_conninfo(database_url: str) -> str:
    """Bersihkan URL: strip + skema SQLAlchemy → skema psycopg murni.

    Hanya ganti PREFIX (bukan replace global — password bisa mengandung
    substring yang sama).
    """
    url = (database_url or "").strip()
    prefix = "postgresql+psycopg://"
    if url.startswith(prefix):
        return "postgresql://" + url[len(prefix):]
    return url


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Buat pool TERTUTUP. Lifespan yang `open()` + `close()` (bukan pemanggil)."""
    url = normalize_conninfo(settings.DATABASE_URL)
    if not url:
        raise RuntimeError(
            "DATABASE_URL kosong. v0.3+ wajib Postgres (tanpa fallback memory). "
            "Jalankan `docker compose up -d app-db` di folder Infra/, lalu isi "
            "DATABASE_URL di .env — contoh di .env.example."
        )
    pool_min = max(1, int(settings.DB_POOL_MIN))
    pool_max = max(pool_min, int(settings.DB_POOL_MAX))
    if pool_min != settings.DB_POOL_MIN or pool_max != settings.DB_POOL_MAX:
        # Jangan sembunyikan salah config (mis. MIN>MAX, nol, negatif).
        log.warning("DB_POOL di-clamp (%s,%s) → (%d,%d); perbaiki env.",
                    settings.DB_POOL_MIN, settings.DB_POOL_MAX, pool_min, pool_max)
    return AsyncConnectionPool(
        url,
        min_size=pool_min,
        max_size=pool_max,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )


async def open_pool(pool: AsyncConnectionPool) -> None:
    """Buka pool (buat koneksi awal). Gagal = biarkan raise (fail-fast)."""
    await pool.open()


def get_checkpointer(pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    """Saver di atas pool bersama. Murah (tanpa I/O) — boleh dipanggil per request."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    return AsyncPostgresSaver(pool)


def get_store(pool: AsyncConnectionPool) -> AsyncPostgresStore:
    """Store mentah di atas pool bersama. Namespace dikunci di MemoryStore."""
    from langgraph.store.postgres.aio import AsyncPostgresStore

    return AsyncPostgresStore(pool)


async def setup_persistence(checkpointer, store) -> None:
    """Buat skema tabel bila belum ada. Idempotent — aman tiap startup.

    Asumsi sadar: SATU proses setup per deploy dalam satu waktu. Dua worker
    yang start persis bersamaan bisa race DDL — untuk template ini diterima
    (restart/retry manual); advisory-lock DB ditunda ke v0.4+ bila terbukti
    jadi masalah operasional.
    """
    await checkpointer.setup()
    await store.setup()


async def close_pool(pool: AsyncConnectionPool) -> None:
    """Tutup pool saat shutdown. Tak pernah raise (shutdown harus bersih)."""
    try:
        await pool.close()
    except Exception:
        # Menelan saat shutdown memang umum, tapi TANPA log = buta (temuan F-04).
        log.warning("close pool gagal (diabaikan agar shutdown bersih)",
                    exc_info=True)
