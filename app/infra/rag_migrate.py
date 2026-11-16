"""Runner migrasi RAG 007 (D18): advisory lock + tabel versi + DDL eksplisit.

Kenapa bukan `ensure_schema IF NOT EXISTS` buta: `CREATE EXTENSION` + HNSW +
RLS + fungsi privileged harus GAGAL EKSPILISIT bila separuh jalan (bukan
"sepertinya sudah ada"). Versi dicatat di `schema_migrations`.

Urutan dijalankan di lifespan startup (API) — worker ingest memanggil fungsi
yang sama (idempotent) agar entrypoint mana pun duluan tetap aman.
"""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)

MIGRATION_VERSION = "007"
MIGRATION_FILE = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "007_rag.sql"
)
# Daftar migrasi BERURUTAN (tambah di sini, JANGAN edit berkas lama).
MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("007", "007_rag.sql"),
    ("008", "008_rag_grants.sql"),
    ("009", "009_rag_lease_fix.sql"),
    ("010", "010_rag_norm_lexical.sql"),
    ("011", "011_kg.sql"),
    ("012", "012_rls_with_check.sql"),
    ("013", "013_kg_alias.sql"),
    ("014", "014_rename_tenant_to_workspace.sql"),
    ("015", "015_fix_secret_enc_grant.sql"),
)
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
# Kunci advisory stabil untuk migrasi RAG (tak boleh dipakai keperluan lain).
ADVISORY_KEY = "rag_migration_007"


def split_statements(sql: str) -> list[str]:
    """Pecah berkas SQL menjadi 1 statement per item.

    Syarat: psycopg extended protocol menolak multi-statement per execute
    (pelajaran v0.6). Splitter sadar: string literal '...', identifier "...",
    dollar-quote $tag$...$tag$, komentar -- dan /* */.
    """
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        # Komentar garis.
        if ch == "-" and nxt == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        # Komentar blok.
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue
        # String literal / quoted identifier (dengan escape '' dan "").
        if ch in ("'", '"'):
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(sql[i])
                if sql[i] == ch:
                    if i + 1 < n and sql[i + 1] == ch:
                        buf.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        # Dollar-quote: $tag$ ... $tag$.
        if ch == "$":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                tag = sql[i:j + 1]
                end = sql.find(tag, j + 1)
                if end == -1:
                    raise ValueError(f"dollar-quote {tag} tak ditutup")
                buf.append(sql[i:end + len(tag)])
                i = end + len(tag)
                continue
            buf.append(ch)
            i += 1
            continue
        # Terminator statement.
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


async def apply_rag_migration(pool) -> bool:
    """Terapkan migrasi yang BELUM teraplikasi, berurutan. Return True bila
    ada yang dieksekusi sekarang.

    Idempotent + aman konkurensi via `pg_advisory_xact_lock` (2 entrypoint
    start bersamaan = 1 menang, 1 melihat versi sudah ada).
    """
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (ADVISORY_KEY,)
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur = await conn.execute("SELECT version FROM schema_migrations")
            have = {r["version"] for r in await cur.fetchall()}
            applied_any = False
            for version, filename in MIGRATIONS:
                if version in have:
                    continue
                statements = split_statements(
                    (MIGRATIONS_DIR / filename).read_text(encoding="utf-8"))
                log.info("migrasi RAG %s: %d statement", version,
                         len(statements))
                for stmt in statements:
                    await conn.execute(stmt)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,))
                applied_any = True
            return applied_any
