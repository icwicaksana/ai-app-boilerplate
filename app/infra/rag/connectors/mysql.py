"""SourceConnector MySQL (D11): cermin Postgres (read-only + watermark).

Dialek: backtick identifier, information_schema TABLE_SCHEMA=DATABASE().
"""

from __future__ import annotations

from typing import Any

from app.infra.rag.connectors.common import (
    check_ident,
    check_readonly_sql,
    format_watermark,
    parse_watermark,
    render_row,
)


class MysqlSourceConnector:
    source_kind = "mysql"

    def __init__(self, *, host: str, port: int, db: str, user: str,
                 password: str, read_only: bool = True,
                 allowed_tables: list[str] | None = None,
                 columns: dict[str, list[str]] | None = None,
                 timeout: int = 10) -> None:
        if not read_only:
            raise ValueError("konektor menolak read_only=False (D11).")
        self._cfg = {"host": host, "port": port, "db": db, "user": user,
                     "password": password, "connect_timeout": timeout}
        # NOTE: aiomysql.connect tak terima read/write_timeout (TypeError) —
        # hanya connect_timeout. Timeout query dijaga statement-level (n/a)
        # + TimeoutError asyncio pemanggil bila perlu.
        self._allowed = ([check_ident(t, kind="table") for t in allowed_tables]
                         if allowed_tables else None)
        self._columns = columns or {}

    def _check_table(self, table: str) -> str:
        check_ident(table, kind="table")
        if self._allowed is not None and table not in self._allowed:
            raise ValueError(f"tabel {table!r} di luar allowlist konektor")
        return table

    async def _pool(self):
        import aiomysql

        return await aiomysql.create_pool(
            minsize=1, maxsize=2, autocommit=True, **self._cfg)

    async def introspect(self) -> list[dict[str, Any]]:
        pool = await self._pool()
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(check_readonly_sql(
                        "SELECT TABLE_NAME AS name,"
                        " TABLE_ROWS AS rows_est"
                        " FROM information_schema.TABLES"
                        " WHERE TABLE_SCHEMA = DATABASE()"
                        " ORDER BY 1"))
                    tables = [{"name": r[0], "rows_est": r[1]}
                              for r in await cur.fetchall()]
                    out = []
                    for t in tables:
                        if (self._allowed is not None
                                and t["name"] not in self._allowed):
                            continue
                        cols = await self._columns_of(cur, t["name"])
                        names = [c["name"] for c in cols]
                        out.append(
                            {"name": t["name"], "columns": cols,
                             "primary_key": ["id"] if "id" in names
                             else names[:1],
                             "row_count": t["rows_est"]})
                    return out
        finally:
            pool.close()
            await pool.wait_closed()

    async def _columns_of(self, cur, table: str) -> list[dict[str, Any]]:
        await cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
            " ORDER BY ORDINAL_POSITION", (table,))
        return [{"name": r[0], "type": r[1]} for r in await cur.fetchall()]

    def _projection(self, table: str) -> str:
        if table in self._columns:
            allow = [check_ident(c, kind="column")
                     for c in self._columns[table]]
            if "updated_at" not in allow or "id" not in allow:
                raise ValueError(
                    f"proyeksi {table} wajib memuat updated_at+id (watermark)")
            return ", ".join(f"`{c}`" for c in allow)
        return "*"

    async def _fetch(self, sql: str, params: tuple, table: str):
        pool = await self._pool()
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SET TRANSACTION READ ONLY")
                    await cur.execute(check_readonly_sql(sql), params)
                    rows = await cur.fetchall()
                    cols = [d[0] for d in (cur.description or [])]
                    return rows, cols
        finally:
            pool.close()
            await pool.wait_closed()

    async def fetch_incremental(self, *, table: str, watermark: str,
                                limit: int) -> tuple[list[dict], str]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit 1..1000")
        self._check_table(table)
        ts, pk = parse_watermark(watermark)
        # MySQL DATETIME tak paham ISO 'T'/zona → normalisasi eksplisit.
        from datetime import datetime

        ts = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M:%S")
        proj = self._projection(table)
        rows, colnames = await self._fetch(
            f"SELECT {proj} FROM `{table}`"
            " WHERE (updated_at, id) > (%s, %s)"
            " ORDER BY updated_at ASC, id ASC LIMIT %s",
            (ts, pk or "0", limit), table)
        records, new_wm = [], watermark
        for row in rows:
            d = dict(zip(colnames, row))
            if d.get("updated_at") is None or d.get("id") is None:
                raise ValueError(
                    f"baris {table} tanpa updated_at/id (kontrak watermark)")
            new_wm = format_watermark(d.get("updated_at"), d.get("id"))
            records.append({
                "ref": f"{table}:{d.get('id')}",
                "text": render_row(table, d, pk_cols=["id"]),
                "watermark": new_wm,
                "deleted": bool("deleted_at" in d and d.get("deleted_at")),
                "metadata": {"table": table, "id": str(d.get("id", ""))}})
        return records, new_wm

    async def fetch_by_id(self, *, table: str, ref: str) -> dict | None:
        self._check_table(table)
        pk = ref.rsplit(":", 1)[-1] if ":" in ref else ref
        rows, colnames = await self._fetch(
            f"SELECT * FROM `{table}` WHERE id = %s", (pk,), table)
        if not rows:
            return None
        d = dict(zip(colnames, rows[0]))
        return {"ref": f"{table}:{d.get('id')}",
                "text": render_row(table, d, pk_cols=["id"]),
                "watermark": format_watermark(d.get("updated_at"),
                                              d.get("id")),
                "deleted": bool("deleted_at" in d and d.get("deleted_at")),
                "metadata": {"table": table, "id": str(d.get("id", ""))}}
