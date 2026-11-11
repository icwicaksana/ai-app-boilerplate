"""SourceConnector Postgres (D11): read-only struktural + watermark.

Lapis pertahanan tulis:
1. Kontrak: TANPA method tulis (by construction) + `read_only` wajib True.
2. Transaksi `READ ONLY` per query + denylist kata tulis (common).
3. Operasional: DB-user read-only (runbook) — konektor MENOLAK bila
   `read_only=False` di config.

Watermark = `(updated_at, id)`; delete = soft-delete (`deleted_at`) ATAU
tombstone eksplisit — hard-delete tanpa tombstone TAK didukung v0.7.
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


class PostgresSourceConnector:
    source_kind = "postgres"

    def __init__(self, dsn: str, *, read_only: bool = True,
                 allowed_tables: list[str] | None = None,
                 columns: dict[str, list[str]] | None = None,
                 timeout: int = 10) -> None:
        if not read_only:
            raise ValueError("konektor menolak read_only=False (D11).")
        self._dsn = dsn
        self._allowed = ([check_ident(t, kind="table") for t in allowed_tables]
                         if allowed_tables else None)
        self._columns = columns or {}
        self._timeout = timeout
        self._meta_cache: dict[str, dict] | None = None

    async def _conn(self):
        import psycopg

        return await psycopg.AsyncConnection.connect(
            self._dsn, connect_timeout=self._timeout, autocommit=True)

    def _check_table(self, table: str) -> str:
        check_ident(table, kind="table")
        if self._allowed is not None and table not in self._allowed:
            raise ValueError(f"tabel {table!r} di luar allowlist konektor")
        return table

    async def introspect(self) -> list[dict[str, Any]]:
        """Struktur sumber (public schema; estimasi baris via pg_class)."""
        import psycopg
        from psycopg.rows import dict_row

        sql = check_readonly_sql(
            "SELECT t.tablename AS name,"
            " (SELECT count(*) FROM information_schema.columns c"
            "  WHERE c.table_schema = 'public' AND c.table_name = t.tablename"
            " ) AS ncol,"
            " (SELECT reltuples::bigint FROM pg_class"
            "  WHERE relname = t.tablename"
            "  AND relnamespace = 'public'::regnamespace) AS rows_est"
            " FROM pg_tables t WHERE schemaname = 'public'"
            " ORDER BY 1")
        conn = await psycopg.AsyncConnection.connect(
            self._dsn, connect_timeout=self._timeout, autocommit=True,
            row_factory=dict_row)
        try:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                cur = await conn.execute(sql)
                tables = [dict(r) for r in await cur.fetchall()]
                out = []
                for t in tables:
                    name = t["name"]
                    if self._allowed is not None and name not in self._allowed:
                        continue
                    cols = await self._columns_of(conn, name)
                    out.append({"name": name, "columns": cols,
                                "primary_key": self._pk_of(cols),
                                "row_count": t["rows_est"]})
                return out
        finally:
            await conn.close()

    async def _columns_of(self, conn, table: str) -> list[dict[str, Any]]:
        cur = await conn.execute(check_readonly_sql(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = %s"
            " ORDER BY ordinal_position"), [table])
        return [{"name": r["column_name"], "type": r["data_type"]}
                for r in await cur.fetchall()]

    @staticmethod
    def _pk_of(cols: list[dict]) -> list[str]:
        names = [c["name"] for c in cols]
        return ["id"] if "id" in names else names[:1]

    def _projection(self, table: str, cols: list[str]) -> str:
        """Kolom SELECT: allowlist config bila ada (F19), else *."""
        if table in self._columns:
            allow = [check_ident(c, kind="column")
                     for c in self._columns[table]]
            if "updated_at" not in allow or "id" not in allow:
                raise ValueError(
                    f"proyeksi {table} wajib memuat updated_at+id (watermark)")
            return ", ".join(f'"{c}"' for c in allow)
        return "*"

    async def fetch_incremental(self, *, table: str, watermark: str,
                                limit: int) -> tuple[list[dict], str]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit 1..1000")
        self._check_table(table)
        ts, pk = parse_watermark(watermark)
        conn = await self._conn()
        try:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                cols = await self._table_cols(conn, table)
                proj = self._projection(table, cols)
                has_deleted = "deleted_at" in cols
                sql = check_readonly_sql(
                    f"SELECT {proj} FROM \"{table}\""
                    " WHERE (updated_at, id) > (%s::timestamptz, %s)"
                    " ORDER BY updated_at ASC, id ASC LIMIT %s")
                cur = await conn.execute(sql, [ts, pk or "0", limit])
                rows = await cur.fetchall()
                colnames = [d.name for d in cur.description or []]
        finally:
            await conn.close()
        records, new_wm = [], watermark
        for row in rows:
            d = dict(zip(colnames, row))
            if d.get("updated_at") is None or d.get("id") is None:
                # Watermark korup lebih buruk dari gagal: tolak keras (kontrak
                # sumber: updated_at NOT NULL + PK `id` integer, v0.7).
                raise ValueError(
                    f"baris {table} tanpa updated_at/id (kontrak watermark)")
            new_wm = format_watermark(d.get("updated_at"), d.get("id"))
            records.append({
                "ref": f"{table}:{d.get('id')}",
                "text": render_row(table, d, pk_cols=["id"]),
                "watermark": new_wm,
                "deleted": bool(has_deleted and d.get("deleted_at")),
                "metadata": {"table": table, "id": str(d.get("id", ""))}})
        return records, new_wm

    async def _table_cols(self, conn, table: str) -> list[str]:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = %s", [table])
        return [r[0] for r in await cur.fetchall()]

    async def fetch_by_id(self, *, table: str, ref: str) -> dict | None:
        self._check_table(table)
        pk = ref.rsplit(":", 1)[-1] if ":" in ref else ref
        conn = await self._conn()
        try:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                cols = await self._table_cols(conn, table)
                has_deleted = "deleted_at" in cols
                cur = await conn.execute(check_readonly_sql(
                    f"SELECT * FROM \"{table}\" WHERE id = %s"), [pk])
                row = await cur.fetchone()
                if row is None:
                    return None
                d = dict(zip([c.name for c in cur.description or []], row))
        finally:
            await conn.close()
        return {"ref": f"{table}:{d.get('id')}",
                "text": render_row(table, d, pk_cols=["id"]),
                "watermark": format_watermark(d.get("updated_at"),
                                              d.get("id")),
                "deleted": bool(has_deleted and d.get("deleted_at")),
                "metadata": {"table": table, "id": str(d.get("id", ""))}}
