"""Checkpointer factory: Postgres jika DATABASE_URL diisi, memory jika kosong.

- Postgres (AsyncPostgresSaver): wajib untuk produksi — snapshot state per node,
  mendukung time-travel + HITL resume lintas restart.
- InMemorySaver: untuk dev cepat tanpa docker. Data hilang saat restart.

Controller/agent tidak boleh tahu backend apa yang dipakai — mereka hanya terima
objek checkpointer dari sini via dependencies.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver


def is_postgres_configured(database_url: str) -> bool:
    return bool(database_url) and "postgres" in database_url.lower()


@lru_cache
def get_checkpointer(database_url: str = ""):
    """Buat (dan cache) checkpointer. Panggil sekali saat startup."""
    if is_postgres_configured(database_url):
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:  # pragma: no cover
            raise ValueError(
                "DATABASE_URL Postgres terisi tapi paket "
                "langgraph-checkpoint-postgres belum terinstall."
            ) from exc
        conn_string = database_url.replace("postgresql+psycopg://", "postgresql://")
        return AsyncPostgresSaver.from_conn_string(conn_string)

    return InMemorySaver()


def clear_checkpointer_cache() -> None:  # untuk test
    get_checkpointer.cache_clear()


async def setup_checkpointer(checkpointer) -> None:
    """Panggil sekali di lifespan startup. InMemorySaver tidak butuh setup."""
    setup = getattr(checkpointer, "setup", None)
    if setup is not None:
        await setup()
