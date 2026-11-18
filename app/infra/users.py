"""Repo user (v0.4): tabel `app_users` di app-db.

Kenapa di Postgres, bukan cache: user adalah **system of record** (ADR-14);
Redis hanya untuk data ephemeral. Query SELALU parameterized — pelajaran dari
CVE checkpointer LangGraph (SQLi lewat string interpolation).
"""

from __future__ import annotations

from typing import Any

from psycopg_pool import AsyncConnectionPool

from app.core.logging import get_logger
from app.core.threads import canon_segment

log = get_logger(__name__)

DEFAULT_SCOPES = "chat jobs threads approve documents"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_users (
    id            BIGSERIAL PRIMARY KEY,
    workspace        TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    scopes        TEXT        NOT NULL DEFAULT 'chat jobs threads approve documents',
    clearance     TEXT        NOT NULL DEFAULT 'public',
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    UNIQUE (workspace, username)
)
"""

# v1.0 S1: thread/memory namespace di-lowercase (canon_segment), tapi UNIQUE
# di atas case-sensitive → `Budi` dan `budi` dua baris yang berbagi thread.
# Index ini menutup kolisi di level DB.
_LOWER_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS app_users_workspace_username_lower_key
    ON app_users (LOWER(workspace), LOWER(username))
"""


def canon_identity(workspace: str, username: str) -> tuple[str, str]:
    """Normalisasi tunggal identitas user (strip+lower+validasi charset).

    Satu-satunya pintu: repo memanggil ini di setiap tulis/baca, sehingga
    `Budi` dan `budi` TIDAK PERNAH jadi dua user berbeda. Raise
    InvalidThreadIdError (400) bila tak memenuhi format segmen.
    """
    return (canon_segment(workspace, "workspace"),
            canon_segment(username, "user"))


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    """Buat tabel bila belum ada (idempotent, dipanggil lifespan)."""
    async with pool.connection() as conn:
        await conn.execute(_SCHEMA)
        # v1.0 022: self-heal kolom clearance untuk DB lama (CREATE TABLE
        # di atas tak menyentuh tabel existing; migrasi 022 jalur utama).
        await conn.execute(
            "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS clearance"
            " TEXT NOT NULL DEFAULT 'public'")
        # v1.0 S1: kanonisasi baris lama + index anti-kolisi. Urutan penting:
        # (1) tabrakan case (`Budi`+`budi`) = gagal boot LOUD dengan remidiasi
        # (operator harus gabung manual — tak ada pilihan otomatis yang aman);
        # (2) baris non-kanonis tunggal di-lowercase (aman, tanpa kehilangan);
        # (3) index unik LOWER menutup lubang untuk selamanya.
        cur = await conn.execute(
            "SELECT LOWER(workspace) AS w, LOWER(username) AS u, COUNT(*) "
            "FROM app_users GROUP BY 1, 2 HAVING COUNT(*) > 1 LIMIT 5")
        dups = await cur.fetchall()
        if dups:
            sample = ", ".join(f"{r['w']}:{r['u']}" for r in dups)
            raise RuntimeError(
                "app_users punya user tabrakan-case "
                f"({sample}) — gabung manual dulu (pilih 1, hapus sisanya, "
                "pindahkan thread/memory), lalu restart.")
        await conn.execute(
            "UPDATE app_users SET workspace = LOWER(TRIM(workspace)), "
            "username = LOWER(TRIM(username)) "
            "WHERE workspace <> LOWER(TRIM(workspace)) "
            "OR username <> LOWER(TRIM(username))")
        await conn.execute(_LOWER_INDEX)
        # v1.0 S4: scope baru "approve"+"documents" untuk baris lama (idempoten;
        # default tim kecil: semua user boleh me-review, SoD tetap ditegakkan
        # di decide — deployment ketat cabut manual dari non-reviewer).
        await conn.execute(
            "ALTER TABLE app_users ALTER COLUMN scopes "
            "SET DEFAULT 'chat jobs threads approve documents'")
        await conn.execute(
            "UPDATE app_users SET scopes = TRIM(scopes || ' approve') "
            "WHERE scopes NOT LIKE '%approve%'")
        await conn.execute(
            "UPDATE app_users SET scopes = TRIM(scopes || ' documents') "
            "WHERE scopes NOT LIKE '%documents%'")
        # v1.0 G6: kolom clearance untuk baris lama (fail-closed: public).
        await conn.execute(
            "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS clearance "
            "TEXT NOT NULL DEFAULT 'public'")
        await conn.execute(
            "UPDATE app_users SET clearance = 'public' "
            "WHERE clearance NOT IN ('public', 'internal', 'restricted')")
    log.info("auth schema ready (app_users)")


async def get_user(pool: AsyncConnectionPool, workspace: str,
                   username: str) -> dict[str, Any] | None:
    workspace, username = canon_identity(workspace, username)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, workspace, username, password_hash, scopes, clearance,"
            " is_active "
            "FROM app_users WHERE workspace = %s AND username = %s",
            (workspace, username),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def create_user(pool: AsyncConnectionPool, *, workspace: str, username: str,
                      password_hash: str, scopes: str = DEFAULT_SCOPES,
                      clearance: str = "public",
                      is_active: bool = True) -> int:
    """Insert user baru; raise ValueError bila sudah ada (unique)."""
    from app.core.acl import normalize_level

    workspace, username = canon_identity(workspace, username)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO app_users (workspace, username, password_hash, scopes, "
            "clearance, is_active) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (workspace, username, password_hash, scopes,
             normalize_level(clearance), is_active),
        )
        row = await cur.fetchone()
    user_id = int(row["id"])
    log.info("user dibuat id=%s workspace=%s username=%s", user_id, workspace, username)
    return user_id


async def mark_login(pool: AsyncConnectionPool, user_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE app_users SET last_login_at = now() WHERE id = %s",
            (user_id,),
        )


async def set_active(pool: AsyncConnectionPool, *, workspace: str, username: str,
                     is_active: bool) -> bool:
    workspace, username = canon_identity(workspace, username)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE app_users SET is_active = %s WHERE workspace = %s AND username = %s",
            (is_active, workspace, username),
        )
        return (cur.rowcount or 0) > 0
