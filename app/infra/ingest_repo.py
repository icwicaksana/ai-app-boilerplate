"""Repo ingest RAG (D12): SQL documents/ingestion_jobs untuk worker + API + test.

Semua query difilter tenant eksplisit + `SET LOCAL app.current_tenant`
(kecuali `claim_job` yang lewat fungsi privileged `claim_ingest_job`).
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Json

JOB_COLUMNS = ("id, tenant, document_id, state, stage, progress, attempts,"
               " max_attempts, next_retry_at, claimed_by, claimed_at,"
               " lease_until, last_error, created_at, updated_at")
DOC_COLUMNS = ("id, tenant, source_type, source_uri, title, lang, bytes,"
               " sha256, parser, chunk_preset, status, meta, created_by,"
               " created_at, updated_at")


async def _one(conn, sql: str, params: list) -> dict | None:
    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    return dict(row) if row else None


async def _all(conn, sql: str, params: list) -> list[dict]:
    cur = await conn.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


def _tenant_conn(pool, tenant: str):
    """Context manager: koneksi + transaksi + SET LOCAL tenant + RLS."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant', %s, true)",
                    (tenant,))
                yield conn
    return _cm()


async def create_document(pool, *, tenant: str, source_type: str,
                          source_uri: str, title: str, lang: str,
                          size_bytes: int, sha256: str, parser: str,
                          chunk_preset: str, created_by: str,
                          meta: dict | None = None,
                          document_id: str | None = None) -> dict:
    """Insert dokumen. Idempotent per (tenant, sha256) → kembalikan existing.

    `document_id` opsional (controller generate dulu agar path blob diketahui
    sebelum insert); None = default gen_random_uuid().

    Race-safe (K7): INSERT ... ON CONFLICT DO NOTHING + SELECT — dua upload
    konkuren file sama → 1 menang, 1 dapat existing (bukan 500).
    """
    async with _tenant_conn(pool, tenant) as conn:
        cols = ["tenant", "source_type", "source_uri", "title", "lang",
                "bytes", "sha256", "parser", "chunk_preset", "status",
                "meta", "created_by"]
        vals: list = [tenant, source_type, source_uri, title, lang,
                      size_bytes, sha256, parser, chunk_preset]
        if document_id is not None:
            cols.insert(0, "id")
            vals.insert(0, document_id)
        marks = ", ".join(["%s"] * len(vals))
        row = await _one(
            conn, f"INSERT INTO documents ({', '.join(cols)})"
                  f" VALUES ({marks}, 'pending', %s, %s)"
                  " ON CONFLICT (tenant, sha256) DO NOTHING"
                  f" RETURNING {DOC_COLUMNS}",
            vals + [Json(meta or {}), created_by])
        if row is None:
            row = await _one(
                conn, f"SELECT {DOC_COLUMNS} FROM documents"
                      " WHERE tenant = %s AND sha256 = %s",
                [tenant, sha256])
            if row is None:  # tak mungkin (konflik = ada); gagal keras jujur
                raise RuntimeError("dedup inkonsisten: konflik tanpa baris")
            row["_existing"] = True
        return row


async def get_document(pool, *, tenant: str, document_id: str) -> dict | None:
    async with _tenant_conn(pool, tenant) as conn:
        return await _one(
            conn, f"SELECT {DOC_COLUMNS} FROM documents"
                  " WHERE tenant = %s AND id = %s",
            [tenant, document_id])


async def list_documents(pool, *, tenant: str, status: str | None,
                         limit: int, offset: int) -> tuple[list[dict], int]:
    async with _tenant_conn(pool, tenant) as conn:
        where, params = "WHERE tenant = %s", [tenant]
        if status:
            where += " AND status = %s"
            params.append(status)
        items = await _all(
            conn, f"SELECT {DOC_COLUMNS} FROM documents {where}"
                  " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            [*params, limit, offset])
        total = (await _one(
            conn, f"SELECT count(*) AS n FROM documents {where}", params))["n"]
        return items, total


async def set_document_status(pool, *, tenant: str, document_id: str,
                              status: str, meta_patch: dict | None = None,
                              ) -> None:
    async with _tenant_conn(pool, tenant) as conn:
        if meta_patch:
            await conn.execute(
                "UPDATE documents SET status = %s, meta = meta || %s::jsonb,"
                " updated_at = now() WHERE tenant = %s AND id = %s",
                [status, Json(meta_patch), tenant, document_id])
        else:
            await conn.execute(
                "UPDATE documents SET status = %s, updated_at = now()"
                " WHERE tenant = %s AND id = %s",
                [status, tenant, document_id])


async def create_job(pool, *, tenant: str, document_id: str,
                     max_attempts: int = 5) -> dict:
    async with _tenant_conn(pool, tenant) as conn:
        return await _one(
            conn, f"INSERT INTO ingestion_jobs (tenant, document_id,"
                  " max_attempts) VALUES (%s, %s, %s)"
                  f" RETURNING {JOB_COLUMNS}",
            [tenant, document_id, max_attempts])


async def get_job(pool, *, tenant: str, job_id: int) -> dict | None:
    async with _tenant_conn(pool, tenant) as conn:
        return await _one(
            conn, f"SELECT {JOB_COLUMNS} FROM ingestion_jobs"
                  " WHERE tenant = %s AND id = %s",
            [tenant, job_id])


async def latest_job_for_document(pool, *, tenant: str,
                                  document_id: str) -> dict | None:
    """Job terbaru 1 dokumen (untuk SSE progress per-dokumen, D12)."""
    async with _tenant_conn(pool, tenant) as conn:
        return await _one(
            conn, f"SELECT {JOB_COLUMNS} FROM ingestion_jobs"
                  " WHERE tenant = %s AND document_id = %s"
                  " ORDER BY id DESC LIMIT 1",
            [tenant, document_id])


async def count_chunks(pool, *, tenant: str, document_id: str) -> int:
    async with _tenant_conn(pool, tenant) as conn:
        row = await _one(
            conn, "SELECT count(*) AS n FROM chunks"
                  " WHERE tenant = %s AND document_id = %s",
            [tenant, document_id])
        return row["n"] if row else 0


async def claim_job(pool, *, worker_id: str,
                 lease_seconds: int = 120) -> dict | None:
    """Klaim 1 job via fungsi privileged (lintas-tenant, SKIP LOCKED).

    Koneksi pendek (<100ms): JANGAN tahan untuk kerja berat (T11).
    """
    async with pool.connection() as conn:
        row = await _one(conn, "SELECT * FROM claim_ingest_job(%s, %s)",
                         [worker_id, lease_seconds])
        if row is None:
            return None
        return await get_job(pool, tenant=row["job_tenant"],
                             job_id=row["job_id"])


async def heartbeat(pool, *, job_id: int, worker_id: str, tenant: str,
                    lease_seconds: int) -> bool:
    """Perpanjang lease. False = hilang/diambil worker lain (fencing K1/P1).

    Filter `claimed_by` + tenant: worker basi TAK BISA perpanjang lease
    milik worker baru. SET LOCAL dijaga (RLS lapis-3).
    """
    async with _tenant_conn(pool, tenant) as conn:
        cur = await conn.execute(
            "UPDATE ingestion_jobs SET lease_until = now() +"
            " make_interval(secs => %s), updated_at = now()"
            " WHERE id = %s AND tenant = %s AND claimed_by = %s"
            " AND state NOT IN ('done', 'failed', 'dead', 'queued')",
            [lease_seconds, job_id, tenant, worker_id])
        return (cur.rowcount or 0) > 0


async def set_stage(pool, *, job_id: int, stage: str, progress: int,
                    tenant: str) -> None:
    """Update progres. `state` TIDAK ditimpa (tetap `leased` sampai terminal;
    menimpa = heartbeat mati + job macet, K1)."""
    async with _tenant_conn(pool, tenant) as conn:
        await conn.execute(
            "UPDATE ingestion_jobs SET stage = %s, progress = %s,"
            " updated_at = now() WHERE id = %s AND tenant = %s",
            [stage, progress, job_id, tenant])


async def finish_job(pool, *, job_id: int, tenant: str,
                     document_id: str) -> bool:
    """Selesai. Guard `status!='deleted'` (race hapus-vs-ingest, K4).

    Return False bila dokumen sudah dihapus (pemenang = delete).
    """
    async with _tenant_conn(pool, tenant) as conn:
        await conn.execute(
            "UPDATE ingestion_jobs SET state = 'done', stage = 'done',"
            " progress = 100, updated_at = now()"
            " WHERE id = %s AND tenant = %s", [job_id, tenant])
        cur = await conn.execute(
            "UPDATE documents SET status = 'ready', updated_at = now()"
            " WHERE id = %s AND tenant = %s AND status != 'deleted'",
            [document_id, tenant])
        return (cur.rowcount or 0) > 0


async def fail_job(pool, *, job_id: int, tenant: str, document_id: str,
                   error: str, retry_at: Any | None,
                   touch_document: bool = True) -> str:
    """Gagal → `failed` (+retry) atau `dead` (habis/terminal).

    Return state akhir ('failed'/'dead'). `retry_at=None` = terminal langsung.
    `touch_document=False` untuk DOCUMENT_GONE (jangan timpa `deleted`, K5).
    """
    async with _tenant_conn(pool, tenant) as conn:
        job = await _one(
            conn, "SELECT attempts, max_attempts FROM ingestion_jobs"
                  " WHERE id = %s AND tenant = %s", [job_id, tenant])
        if job is None:
            raise RuntimeError(f"fail_job: job {job_id} hilang")
        if retry_at is None or job["attempts"] >= job["max_attempts"]:
            await conn.execute(
                "UPDATE ingestion_jobs SET state = 'dead', last_error = %s,"
                " updated_at = now() WHERE id = %s AND tenant = %s",
                [error, job_id, tenant])
            if touch_document:
                await conn.execute(
                    "UPDATE documents SET status = 'failed',"
                    " updated_at = now() WHERE id = %s AND tenant = %s"
                    " AND status != 'deleted'", [document_id, tenant])
            return "dead"
        await conn.execute(
            "UPDATE ingestion_jobs SET state = 'failed', last_error = %s,"
            " next_retry_at = %s, updated_at = now()"
            " WHERE id = %s AND tenant = %s",
            [error, retry_at, job_id, tenant])
        return "failed"


async def cancel_jobs_for_document(pool, *, tenant: str,
                                   document_id: str) -> int:
    """Batalkan job non-terminal 1 dokumen (dipanggil delete, K4)."""
    async with _tenant_conn(pool, tenant) as conn:
        cur = await conn.execute(
            "UPDATE ingestion_jobs SET state = 'dead',"
            " last_error = 'DOCUMENT_DELETED: dokumen dihapus operator',"
            " updated_at = now() WHERE tenant = %s AND document_id = %s"
            " AND state NOT IN ('done', 'dead')",
            [tenant, document_id])
        return cur.rowcount or 0


async def retry_job(pool, *, tenant: str, job_id: int) -> dict | None:
    """Reset job failed/dead → queued (endpoint retry). None bila tak boleh."""
    async with _tenant_conn(pool, tenant) as conn:
        row = await _one(
            conn, "UPDATE ingestion_jobs SET state = 'queued', stage = 'queued',"
                  " progress = 0, attempts = 0, next_retry_at = NULL,"
                  " last_error = NULL, updated_at = now()"
                  " WHERE tenant = %s AND id = %s"
                  " AND state IN ('failed', 'dead')"
                  f" RETURNING {JOB_COLUMNS}",
            [tenant, job_id])
        if row:
            await conn.execute(
                "UPDATE documents SET status = 'pending', updated_at = now()"
                " WHERE id = %s AND tenant = %s",
                [row["document_id"], tenant])
        return row


async def release_lease(pool, *, job_id: int) -> None:
    """Lepas lease saat shutdown (JANGAN hapus job durable, F-03).

    Meliputi SEMUA non-terminal (leased + kerja: 009 tak lagi menimpa state,
    tapi baris lama/transition tetap ter-cover).
    """
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE ingestion_jobs SET lease_until = now(), claimed_by = NULL,"
            " updated_at = now() WHERE id = %s"
            " AND state NOT IN ('done', 'failed', 'dead', 'queued')",
            [job_id])


async def tombstone_document(pool, *, tenant: str,
                             document_id: str) -> bool:
    """Tandai deleted (worker/API menghapus chunks + vektor dulu)."""
    async with _tenant_conn(pool, tenant) as conn:
        cur = await conn.execute(
            "UPDATE documents SET status = 'deleted', updated_at = now()"
            " WHERE tenant = %s AND id = %s AND status != 'deleted'",
            [tenant, document_id])
        return (cur.rowcount or 0) > 0
