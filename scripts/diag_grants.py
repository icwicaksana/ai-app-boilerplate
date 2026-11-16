"""Diagnosa grant kolom secret_enc (penyebab 014 verify gagal)."""

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, ".")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("../Infra/.env")
load_dotenv(".env")


async def main():
    import psycopg

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://",
                                             "postgresql://")
    async with await psycopg.AsyncConnection.connect(url) as conn:
        # 1) Apakah migrasi 014 ter-rollback?
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='source_connections'"
            " AND column_name IN ('tenant','workspace')")
        print("kolom source_connections:",
              [r[0] for r in await cur.fetchall()])
        cur = await conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version")
        print("migrasi:", [r[0] for r in await cur.fetchall()])

        # 2) Grant level kolom pada source_connections
        cur = await conn.execute(
            "SELECT grantee, privilege_type, column_name"
            " FROM information_schema.column_privileges"
            " WHERE table_name='source_connections'"
            " ORDER BY grantee, column_name")
        for r in await cur.fetchall():
            print("  colpriv:", r)

        # 3) Grant level tabel
        cur = await conn.execute(
            "SELECT grantee, privilege_type"
            " FROM information_schema.table_privileges"
            " WHERE table_name='source_connections'"
            " ORDER BY grantee")
        for r in await cur.fetchall():
            print("  tblpriv:", r)

        # 4) attacl mentah (sumber kebenaran)
        cur = await conn.execute(
            "SELECT a.attname, a.attacl FROM pg_attribute a"
            " JOIN pg_class c ON c.oid=a.attrelid"
            " WHERE c.relname='source_connections' AND a.attacl IS NOT NULL")
        rows = await cur.fetchall()
        print("attacl mentah:", rows or "tidak ada")
    return 0


sys.exit(asyncio.run(main()))
