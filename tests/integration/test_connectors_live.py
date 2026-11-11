"""Live SourceConnector: Postgres (scratch table) + MySQL (skip bila mati)."""

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_pg_connector_incremental(db_url):
    from app.core.config import Settings
    from app.infra.postgres import (
        close_pool,
        create_pool,
        normalize_conninfo,
        open_pool,
    )
    from app.infra.rag.connectors.postgres import PostgresSourceConnector

    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5)
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        async with pool.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS it_src_items")
            await conn.execute(
                "CREATE TABLE it_src_items (id SERIAL PRIMARY KEY,"
                " updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " name TEXT NOT NULL, price INT NOT NULL,"
                " deleted_at TIMESTAMPTZ)")
            await conn.execute(
                "INSERT INTO it_src_items (name, price) VALUES"
                " ('Kopi Arabika', 50000), ('Teh Melati', 25000)")
        dsn = normalize_conninfo(db_url)
        conn = PostgresSourceConnector(dsn, allowed_tables=["it_src_items"])
        tables = await conn.introspect()
        names = [t["name"] for t in tables]
        assert names == ["it_src_items"]
        assert tables[0]["primary_key"] == ["id"]

        recs, wm1 = await conn.fetch_incremental(
            table="it_src_items", watermark="", limit=10)
        assert len(recs) == 2
        assert "Kopi Arabika" in recs[0]["text"]
        assert recs[0]["ref"].startswith("it_src_items:")
        assert recs[0]["deleted"] is False

        recs2, wm2 = await conn.fetch_incremental(
            table="it_src_items", watermark=wm1, limit=10)
        assert recs2 == [] and wm2 == wm1  # tak ada yang baru

        one = await conn.fetch_by_id(table="it_src_items", ref=recs[0]["ref"])
        assert one is not None and "Kopi" in one["text"]
        assert await conn.fetch_by_id(table="it_src_items",
                                      ref="it_src_items:99999") is None

        async with pool.connection() as conn2:
            await conn2.execute(
                "INSERT INTO it_src_items (name, price) VALUES ('Susu', 18000)")
            await conn2.execute(
                "UPDATE it_src_items SET deleted_at = now(),"
                " updated_at = now() WHERE name = 'Teh Melati'")
        recs3, _ = await conn.fetch_incremental(
            table="it_src_items", watermark=wm1, limit=10)
        # Baris baru (Susu) muncul; Teh (updated_at berubah) muncul sbg deleted.
        by_name = {r["ref"]: r for r in recs3}
        assert any("Susu" in r["text"] for r in recs3)
        teh = [r for r in recs3 if "Teh Melati" in r["text"]]
        assert teh and teh[0]["deleted"] is True
    finally:
        try:
            async with pool.connection() as conn:
                await conn.execute("DROP TABLE IF EXISTS it_src_items")
        finally:
            await close_pool(pool)


@pytest.mark.asyncio
async def test_live_mysql_connector_incremental():
    """MySQL via service compose (profil mysql). Skip bila mati/belum init."""
    import re
    from pathlib import Path

    from app.infra.rag.connectors.mysql import MysqlSourceConnector

    dsn = (os.environ.get("TEST_MYSQL_DSN", "")).strip()
    if not dsn:
        # Layout repo: kredensial dev di Infra/.env (dibaca eksplisit).
        infra_env = Path(__file__).resolve().parents[3] / "Infra" / ".env"
        pw = ""
        if infra_env.is_file():
            m = re.search(r"(?m)^MYSQL_READER_PASSWORD=(\S+)\s*$",
                          infra_env.read_text(encoding="utf-8"))
            pw = m.group(1) if m else ""
        if not pw:
            pytest.skip("MYSQL_READER_PASSWORD tak ada (Infra/.env)")
        import urllib.parse

        dsn = ("mysql://reader:" + urllib.parse.quote(pw, safe="") +
               "@127.0.0.1:3307/sourcedb")
    import urllib.parse

    import aiomysql

    u = urllib.parse.urlparse(dsn if "://" in dsn else "mysql://" + dsn)
    cfg = {"host": u.hostname or "127.0.0.1", "port": u.port or 3307,
           "db": (u.path or "/sourcedb").lstrip("/"), "user": u.username or "",
           "password": urllib.parse.unquote(u.password or "")}
    try:
        seed = await aiomysql.connect(connect_timeout=3, autocommit=True,
                                      **cfg)
    except Exception:
        pytest.skip("service mysql mati (profil mysql belum up?)")
    try:
        async with seed.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS it_src_items")
            await cur.execute(
                "CREATE TABLE it_src_items (id INT AUTO_INCREMENT PRIMARY KEY,"
                " updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                " ON UPDATE CURRENT_TIMESTAMP,"
                " name VARCHAR(200) NOT NULL, price INT NOT NULL,"
                " deleted_at TIMESTAMP NULL)")
            await cur.execute(
                "INSERT INTO it_src_items (name, price) VALUES"
                " ('Kopi Arabika', 50000), ('Teh Melati', 25000)")
    finally:
        seed.close()
    try:
        conn = MysqlSourceConnector(allowed_tables=["it_src_items"], **cfg)
        tables = await conn.introspect()
        assert [t["name"] for t in tables] == ["it_src_items"]
        recs, wm1 = await conn.fetch_incremental(
            table="it_src_items", watermark="", limit=10)
        assert len(recs) == 2 and "Kopi Arabika" in recs[0]["text"]
        recs2, wm2 = await conn.fetch_incremental(
            table="it_src_items", watermark=wm1, limit=10)
        assert recs2 == [] and wm2 == wm1
        one = await conn.fetch_by_id(table="it_src_items", ref=recs[0]["ref"])
        assert one is not None and "Kopi" in one["text"]
    finally:
        seed = await aiomysql.connect(connect_timeout=3, autocommit=True,
                                      **cfg)
        try:
            async with seed.cursor() as cur:
                await cur.execute("DROP TABLE IF EXISTS it_src_items")
        finally:
            seed.close()
