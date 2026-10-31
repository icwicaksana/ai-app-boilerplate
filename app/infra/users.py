"""Repo user (v0.4): tabel `app_users` di app-db.

Kenapa di Postgres, bukan cache: user adalah **system of record** (ADR-14);
Redis hanya untuk data ephemeral. Query SELALU parameterized — pelajaran dari
CVE checkpointer LangGraph (SQLi lewat string interpolation).
"""

from __future__ import annotations

from typing import Any

from psycopg_pool import AsyncConnectionPool

from app.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_SCOPES = "chat jobs threads"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_users (
    id            BIGSERIAL PRIMARY KEY,
    tenant        TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    scopes        TEXT        NOT NULL DEFAULT 'chat jobs threads',
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    UNIQUE (tenant, username)
)
"""


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    """Buat tabel bila belum ada (idempotent, dipanggil lifespan)."""
    async with pool.connection() as conn:
        await conn.execute(_SCHEMA)
    log.info("auth schema ready (app_users)")


async def get_user(pool: AsyncConnectionPool, tenant: str,
                   username: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, tenant, username, password_hash, scopes, is_active "
            "FROM app_users WHERE tenant = %s AND username = %s",
            (tenant, username),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def create_user(pool: AsyncConnectionPool, *, tenant: str, username: str,
                      password_hash: str, scopes: str = DEFAULT_SCOPES,
                      is_active: bool = True) -> int:
    """Insert user baru; raise ValueError bila sudah ada (unique)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO app_users (tenant, username, password_hash, scopes, "
            "is_active) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (tenant, username, password_hash, scopes, is_active),
        )
        row = await cur.fetchone()
    user_id = int(row["id"])
    log.info("user dibuat id=%s tenant=%s username=%s", user_id, tenant, username)
    return user_id


async def mark_login(pool: AsyncConnectionPool, user_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE app_users SET last_login_at = now() WHERE id = %s",
            (user_id,),
        )


async def set_active(pool: AsyncConnectionPool, *, tenant: str, username: str,
                     is_active: bool) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE app_users SET is_active = %s WHERE tenant = %s AND username = %s",
            (is_active, tenant, username),
        )
        return (cur.rowcount or 0) > 0
