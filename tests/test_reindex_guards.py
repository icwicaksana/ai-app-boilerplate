"""v1.0 V1-D/3: RLS/GUC worker (G3) + tolak campur-model (G4)."""

from __future__ import annotations

import pytest

from app.infra import ingest_repo as repo


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, pool, rows):
        self._pool = pool
        self._rows = rows

    def transaction(self):
        pool = self._pool

        class _Tx:
            async def __aenter__(self):
                pool.tx += 1
                return None

            async def __aexit__(self, *a):
                return False

        return _Tx()

    async def execute(self, sql, params=None):
        self._pool.sql.append((" ".join(str(sql).split()), params))
        return _Cur(self._rows)


class _Pool:
    def __init__(self, rows):
        self._rows = rows
        self.sql: list = []
        self.tx = 0

    def connection(self):
        pool, rows = self, self._rows

        class _Ctx:
            async def __aenter__(self):
                return _Conn(pool, rows)

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_g3_row_ids_set_local_dulu():
    """chunk_row_ids: SET LOCAL + RLS dalam 1 transaksi (bukan query mentah)."""
    pool = _Pool([{"id": "r1", "ord": 0}, {"id": "r2", "ord": 3}])
    out = await repo.chunk_row_ids(pool, workspace="toko-a", doc_id="d1")
    assert out == {0: "r1", 3: "r2"}
    assert pool.tx == 1
    assert pool.sql[0][0].startswith("SELECT set_config(")
    assert pool.sql[0][1] == ("toko-a",)
    assert "WHERE workspace = %s AND document_id = %s" in pool.sql[1][0]


@pytest.mark.asyncio
async def test_g3_reindex_tolak_role_app():
    """_assert_bypassrls: non-bypass → SystemExit LOUD (anti sukses-semu)."""
    from app.workers.reindex import _assert_bypassrls

    await _assert_bypassrls(_Pool([{"s": True, "b": False}]))  # superuser ok
    await _assert_bypassrls(_Pool([{"s": False, "b": True}]))  # bypass ok
    with pytest.raises(SystemExit, match="BYPASSRLS"):
        await _assert_bypassrls(_Pool([{"s": False, "b": False}]))


@pytest.mark.asyncio
async def test_g4_tolak_campur_model_atau_dimensi():
    """Index campur (model/dim) → REFUSE (butuh re-embed, bukan reindex)."""
    from types import SimpleNamespace

    from app.workers.reindex import _pg_dims

    settings = SimpleNamespace(RAG_EMBED_MODEL="BAAI/bge-m3")
    mixed = [{"embedding_model": "BAAI/bge-m3", "embedding_dim": 1024,
              "n": 10},
             {"embedding_model": "other/model", "embedding_dim": 1024,
              "n": 2}]
    with pytest.raises(SystemExit, match="campur"):
        await _pg_dims(_Pool(mixed), settings)
    mixed_dim = [{"embedding_model": "BAAI/bge-m3", "embedding_dim": 1024,
                  "n": 10},
                 {"embedding_model": "BAAI/bge-m3", "embedding_dim": 768,
                  "n": 2}]
    with pytest.raises(SystemExit, match="campur"):
        await _pg_dims(_Pool(mixed_dim), settings)


@pytest.mark.asyncio
async def test_g4_tolak_model_beda_dari_pin():
    from types import SimpleNamespace

    from app.workers.reindex import _pg_dims

    settings = SimpleNamespace(RAG_EMBED_MODEL="BAAI/bge-m3")
    rows = [{"embedding_model": "other/model", "embedding_dim": 1024, "n": 5}]
    with pytest.raises(SystemExit, match="!= pin"):
        await _pg_dims(_Pool(rows), settings)


@pytest.mark.asyncio
async def test_g4_ok_dan_kosong():
    from types import SimpleNamespace

    from app.workers.reindex import _pg_dims

    settings = SimpleNamespace(RAG_EMBED_MODEL="BAAI/bge-m3")
    rows = [{"embedding_model": "BAAI/bge-m3", "embedding_dim": 1024, "n": 5}]
    assert await _pg_dims(_Pool(rows), settings) == 1024
    assert await _pg_dims(_Pool([]), settings) == 1024  # index kosong
