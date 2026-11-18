"""Live G3: RLS/GUC di bawah role NON-bypass (lubang terverifikasi P0-3).

Bukti: (a) SELECT mentah tanpa GUC = 0 baris (RLS menegakkan); (b) repo
chunk_row_ids (transaksi + SET LOCAL) = baris terlihat; (c) reindex
menolak role ini (anti sukses-semu).
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

from app.infra import ingest_repo as repo  # noqa: E402
from app.infra.postgres import close_pool, create_pool, open_pool  # noqa: E402
from app.infra.rag_migrate import apply_rag_migration  # noqa: E402
from app.workers.reindex import _assert_bypassrls  # noqa: E402


def _swap_creds(db_url: str, user: str, password: str) -> str:
    p = urlparse(db_url)
    netloc = f"{user}:{password}@{p.hostname}"
    if p.port:
        netloc += f":{p.port}"
    return urlunparse((p.scheme, netloc, p.path, "", "", ""))


@pytest_asyncio.fixture
async def pools(db_url, tag):
    from app.core.config import Settings

    role = f"rls_t_{tag}"[:30].replace("-", "_")
    password = f"pw_{uuid.uuid4().hex[:12]}"
    admin = create_pool(Settings(_env_file=None, DATABASE_URL=db_url,
                                 DB_POOL_MIN=1, DB_POOL_MAX=2))
    await open_pool(admin)
    await apply_rag_migration(admin)
    async with admin.connection() as conn:
        await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
        await conn.execute(
            f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
        await conn.execute(f'GRANT CONNECT ON DATABASE "{conn.info.dbname}"'
                           f' TO "{role}"')
        await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await conn.execute(f'GRANT SELECT ON chunks TO "{role}"')
    limited = create_pool(Settings(
        _env_file=None, DATABASE_URL=_swap_creds(db_url, role, password),
        DB_POOL_MIN=1, DB_POOL_MAX=2))
    await open_pool(limited)
    yield {"admin": admin, "limited": limited, "role": role,
           "workspace": f"it-rls-{tag}"}
    await close_pool(limited)
    try:
        async with admin.connection() as conn:
            await conn.execute(
                "DELETE FROM chunks WHERE workspace = %s",
                (f"it-rls-{tag}",))
            await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
    finally:
        await close_pool(admin)


async def _seed(admin_pool, workspace: str) -> str:
    doc_id = str(uuid.uuid4())
    async with admin_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO chunks (workspace, document_id, ord, content, meta)"
            " VALUES (%s, %s, 0, 'isi rls', '{}')",
            (workspace, doc_id))
    return doc_id


async def test_select_mentah_nol_chunk_row_ids_terlihat(pools):
    """RLS menegakkan (mentah=0) + helper repo bekerja (SET LOCAL)."""
    admin, limited = pools["admin"], pools["limited"]
    ws = pools["workspace"]
    doc_id = await _seed(admin, ws)
    async with limited.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM chunks WHERE workspace = %s", (ws,))
        assert (await cur.fetchone())["n"] == 0  # tanpa GUC: tak terlihat
    got = await repo.chunk_row_ids(limited, workspace=ws, doc_id=doc_id)
    assert list(got) == [0]  # via SET LOCAL: terlihat


async def test_reindex_tolak_role_non_bypass(pools):
    with pytest.raises(SystemExit, match="BYPASSRLS"):
        await _assert_bypassrls(pools["limited"])
