"""Verifikasi fakta kunci v0.8.2 (klaim expert)."""

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
        cur = await conn.execute(
            "SELECT to_regclass('public.store') IS NOT NULL")
        has = (await cur.fetchone())[0]
        print("tabel store ada:", has)
        if has:
            cur = await conn.execute(
                "SELECT prefix, count(*) FROM store GROUP BY prefix"
                " ORDER BY 2 DESC LIMIT 5")
            for r in await cur.fetchall():
                print(f"  prefix={r[0]!r} n={r[1]}")
            cur = await conn.execute(
                "SELECT count(*) FROM store WHERE prefix LIKE 'tenants.%'")
            print("  ber-separator titik:", (await cur.fetchone())[0])
            cur = await conn.execute(
                "SELECT count(*) FROM store WHERE prefix LIKE 'tenants/%'")
            print("  ber-separator slash:", (await cur.fetchone())[0])

        # prefix ontologi tersimpan
        for tbl, col in (("kg_entities", "type"), ("kg_edges", "relation")):
            cur = await conn.execute(
                f"SELECT count(*) FROM {tbl} WHERE {col} LIKE 'tenant:%'")
            n_old = (await cur.fetchone())[0]
            cur = await conn.execute(
                f"SELECT count(*) FROM {tbl} WHERE {col} LIKE 'workspace:%'")
            n_new = (await cur.fetchone())[0]
            print(f"{tbl}.{col}: tenant:={n_old} workspace:={n_new}")

        # EXECUTE get_source_secret untuk PUBLIC
        cur = await conn.execute(
            "SELECT has_function_privilege('public',"
            " 'public.get_source_secret(uuid)', 'EXECUTE')")
        print("PUBLIC boleh EXECUTE get_source_secret:",
              (await cur.fetchone())[0])
        cur = await conn.execute(
            "SELECT has_function_privilege('public',"
            " 'public.claim_ingest_job(text,int)', 'EXECUTE')")
        print("PUBLIC boleh EXECUTE claim_ingest_job:",
              (await cur.fetchone())[0])
    return 0


sys.exit(asyncio.run(main()))
