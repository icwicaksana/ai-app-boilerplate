"""Penyimpanan approval + audit trail hash-chain (v0.6, ADR-23/ADR-25).

Dua tabel di app-db (system of record):
- `approvals` — satu baris per interrupt yang menunggu keputusan. UNIQUE
  `(thread_id, interrupt_id)` membuat resume **idempoten**.
- `audit_log` — append-only, tiap baris menyimpan `prev_hash` + `hash`
  (SHA-256 dari isi kanonis + prev_hash) sehingga penghapusan/penyuntingan
  bisa dideteksi (`verify_chain`).

Semua query parameterized (pelajaran CVE checkpointer LangGraph).
"""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg_pool import AsyncConnectionPool

from app.core.hitl import canonical_json
from app.core.logging import get_logger

log = get_logger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
GENESIS = "GENESIS"

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id              BIGSERIAL PRIMARY KEY,
        tenant          TEXT        NOT NULL,
        thread_id       TEXT        NOT NULL,
        interrupt_id    TEXT        NOT NULL,
        agent           TEXT        NOT NULL DEFAULT 'example_chat',
        tool_name       TEXT        NOT NULL,
        action_hash     TEXT        NOT NULL,
        action_snapshot JSONB       NOT NULL,
        policy_version  TEXT        NOT NULL,
        status          TEXT        NOT NULL DEFAULT 'pending',
        expires_at      TIMESTAMPTZ NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        decided_by      TEXT,
        decided_at      TIMESTAMPTZ,
        decision        TEXT,
        reason          TEXT,
        UNIQUE (thread_id, interrupt_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS approvals_pending_idx
        ON approvals (tenant, status, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id           BIGSERIAL PRIMARY KEY,
        tenant       TEXT        NOT NULL,
        actor        TEXT        NOT NULL,
        action       TEXT        NOT NULL,
        subject_type TEXT        NOT NULL,
        subject_id   TEXT        NOT NULL,
        payload      JSONB       NOT NULL,
        request_id   TEXT        NOT NULL DEFAULT '-',
        prev_hash    TEXT        NOT NULL,
        hash         TEXT        NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS audit_tenant_idx ON audit_log (tenant, id)
    """,
)


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    # Satu statement per execute: pool memakai extended protocol
    # (prepare_threshold=0) yang menolak banyak perintah dalam satu query.
    async with pool.connection() as conn:
        for statement in _SCHEMA_STATEMENTS:
            await conn.execute(statement)
    log.info("hitl schema ready (approvals, audit_log)")


# --- approvals ---

async def record_pending(pool: AsyncConnectionPool, *, tenant: str,
                         thread_id: str, interrupt_id: str, agent: str,
                         tool_name: str,
                         action_hash: str, action_snapshot: dict[str, Any],
                         policy_version: str, expires_in_seconds: int) -> dict[str, Any]:
    """Catat approval pending (idempotent: satu baris per thread+interrupt)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO approvals (tenant, thread_id, interrupt_id, agent,
                                   tool_name, action_hash, action_snapshot,
                                   policy_version, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                    now() + make_interval(secs => %s))
            ON CONFLICT (thread_id, interrupt_id) DO UPDATE
                SET action_hash = EXCLUDED.action_hash,
                    action_snapshot = EXCLUDED.action_snapshot
                WHERE approvals.status = 'pending'
            RETURNING id, status, action_hash
            """,
            (tenant, thread_id, interrupt_id, agent, tool_name, action_hash,
             canonical_json(action_snapshot), policy_version,
             int(expires_in_seconds)),
        )
        row = await cur.fetchone()
        if row is None:  # sudah diputuskan → ambil baris lama
            cur = await conn.execute(
                "SELECT id, status, action_hash FROM approvals "
                "WHERE thread_id = %s AND interrupt_id = %s",
                (thread_id, interrupt_id))
            row = await cur.fetchone()
    return dict(row) if row else {}


async def get_approval(pool: AsyncConnectionPool, *, tenant: str,
                       approval_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM approvals WHERE tenant = %s AND id = %s",
            (tenant, approval_id))
        row = await cur.fetchone()
    return dict(row) if row else None


async def list_approvals(pool: AsyncConnectionPool, *, tenant: str,
                         status: str | None = STATUS_PENDING,
                         limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        if status:
            cur = await conn.execute(
                "SELECT * FROM approvals WHERE tenant = %s AND status = %s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (tenant, status, limit, offset))
        else:
            cur = await conn.execute(
                "SELECT * FROM approvals WHERE tenant = %s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (tenant, limit, offset))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def claim_decision(pool: AsyncConnectionPool, *, tenant: str,
                         approval_id: int, decision: str, actor: str,
                         reason: str | None) -> dict[str, Any] | None:
    """Klaim keputusan secara ATOMIK (anti double-approve).

    Return baris bila berhasil diklaim; None bila sudah diputuskan/kedaluwarsa.
    """
    new_status = STATUS_APPROVED if decision == "approve" else STATUS_REJECTED
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE approvals
               SET status = %s, decision = %s, decided_by = %s,
                   decided_at = now(), reason = %s
             WHERE tenant = %s AND id = %s AND status = 'pending'
               AND expires_at > now()
            RETURNING *
            """,
            (new_status, decision, actor, reason, tenant, approval_id))
        row = await cur.fetchone()
    return dict(row) if row else None


async def expire_stale(pool: AsyncConnectionPool, *, tenant: str | None = None) -> int:
    """Tandai approval kedaluwarsa (dipanggil saat list/decide)."""
    async with pool.connection() as conn:
        if tenant:
            cur = await conn.execute(
                "UPDATE approvals SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at <= now() AND tenant = %s",
                (tenant,))
        else:
            cur = await conn.execute(
                "UPDATE approvals SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at <= now()")
        return int(cur.rowcount or 0)


# --- audit chain ---

def compute_hash(prev_hash: str, body: dict[str, Any]) -> str:
    raw = f"{prev_hash}:{canonical_json(body)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def append_audit(pool: AsyncConnectionPool, *, tenant: str, actor: str,
                       action: str, subject_type: str, subject_id: str,
                       payload: dict[str, Any], request_id: str = "-") -> str:
    """Tambah entri audit dengan hash chain (advisory lock per tenant)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                               (tenant,))
            cur = await conn.execute(
                "SELECT hash FROM audit_log WHERE tenant = %s "
                "ORDER BY id DESC LIMIT 1", (tenant,))
            row = await cur.fetchone()
            prev_hash = str(row["hash"]) if row else GENESIS
            body = {
                "tenant": tenant, "actor": actor, "action": action,
                "subject_type": subject_type, "subject_id": subject_id,
                "payload": payload, "request_id": request_id,
            }
            new_hash = compute_hash(prev_hash, body)
            await conn.execute(
                """
                INSERT INTO audit_log (tenant, actor, action, subject_type,
                                       subject_id, payload, request_id,
                                       prev_hash, hash)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (tenant, actor, action, subject_type, subject_id,
                 canonical_json(payload), request_id, prev_hash, new_hash))
    return new_hash


async def list_audit(pool: AsyncConnectionPool, *, tenant: str,
                     limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM audit_log WHERE tenant = %s "
            "ORDER BY id ASC LIMIT %s OFFSET %s", (tenant, limit, offset))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def verify_chain(pool: AsyncConnectionPool, *, tenant: str) -> tuple[bool, int | None]:
    """Verifikasi integritas hash chain. Return (ok, id_pertama_yang_rusak)."""
    rows = await list_audit(pool, tenant=tenant, limit=10_000, offset=0)
    prev = GENESIS
    for row in rows:
        body = {
            "tenant": row["tenant"], "actor": row["actor"],
            "action": row["action"], "subject_type": row["subject_type"],
            "subject_id": row["subject_id"], "payload": row["payload"],
            "request_id": row["request_id"],
        }
        if row["prev_hash"] != prev or compute_hash(prev, body) != row["hash"]:
            return False, int(row["id"])
        prev = row["hash"]
    return True, None
