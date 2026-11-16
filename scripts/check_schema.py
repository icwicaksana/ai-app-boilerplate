"""Cek konektivitas DB + status skema sebelum migrasi 014 (F4a)."""

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


async def main():
    import psycopg

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://",
                                             "postgresql://")
    try:
        conn = await psycopg.AsyncConnection.connect(url,
                                                     connect_timeout=8)
    except Exception as exc:
        print(f"DB TIDAK TERJANGKAU: {type(exc).__name__}: {exc}")
        return 1
    try:
        async with conn:
            cur = await conn.execute("SELECT version()")
            print("server:", (await cur.fetchone())[0][:60])
            cur = await conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version")
            print("migrasi tercatat:",
                  [r[0] for r in await cur.fetchall()])
            print("\ntabel | punya tenant | punya workspace")
            for t in TABLES:
                cur = await conn.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = %s AND column_name IN"
                    " ('tenant','workspace')", (t,))
                cols = {r[0] for r in await cur.fetchall()}
                print(f"  {t:20s} {'tenant' in cols!s:5s} "
                      f"{'workspace' in cols!s:5s}")
            cur = await conn.execute(
                "SELECT polname FROM pg_policy ORDER BY polname")
            print("\npolicy:", [r[0] for r in await cur.fetchall()])
    finally:
        await conn.close()
    return 0


sys.exit(asyncio.run(main()))
