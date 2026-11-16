"""Terapkan migrasi RAG (termasuk 014) lalu verifikasi skema (F4b).

Idempoten: `apply_rag_migration` melewati versi yang sudah tercatat.
Dijalankan manual, bukan bagian startup, agar gate bisa diperiksa.
"""

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, ".")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("../Infra/.env")
load_dotenv(".env")

TABLES = ["documents", "ingestion_jobs", "chunks", "source_connections",
          "source_watermarks", "kg_entities", "kg_edges", "kg_aliases",
          "approvals", "audit_log", "app_users"]


def _col(row, name: str, idx: int):
    """Pool memakai dict_row; koneksi mentah memakai tuple. Dukung keduanya."""
    return row[name] if isinstance(row, dict) else row[idx]


async def report(conn, label: str) -> None:
    cur = await conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version")
    vers = [_col(r, "version", 0) for r in await cur.fetchall()]
    print(f"[{label}] migrasi: {vers}")
    bad = []
    for t in TABLES:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = %s AND column_name IN"
            " ('tenant','workspace')", (t,))
        cols = {_col(r, "column_name", 0) for r in await cur.fetchall()}
        state = ("tenant" if "tenant" in cols else
                 "workspace" if "workspace" in cols else "TIDAK ADA")
        if state != "workspace":
            bad.append(f"{t}={state}")
    print(f"[{label}] tabel belum workspace: {bad or 'tidak ada'}")
    cur = await conn.execute("SELECT polname FROM pg_policy ORDER BY polname")
    print(f"[{label}] policy: "
          f"{[_col(r, 'polname', 0) for r in await cur.fetchall()]}")


async def main() -> int:
    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag_migrate import apply_rag_migration

    settings = Settings(_env_file=None,
                        DATABASE_URL=os.environ["DATABASE_URL"],
                        DB_POOL_MIN=1, DB_POOL_MAX=2)
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        async with pool.connection() as conn:
            await report(conn, "SEBELUM")
        await apply_rag_migration(pool)
        async with pool.connection() as conn:
            await report(conn, "SESUDAH")
    finally:
        await close_pool(pool)
    return 0


sys.exit(asyncio.run(main()))
