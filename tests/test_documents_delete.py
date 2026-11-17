"""v1.0 V1-C/4: delete best-effort (R9) + reuse-or-create atomik (R10)."""

from __future__ import annotations

import pytest

from app.controllers.DocumentController import DocumentController
from app.infra import ingest_repo as repo
from tests._auth_helpers import test_settings as _settings


def _ctrl():
    return DocumentController(_settings(), pool=None, rag_clients=None)


@pytest.mark.asyncio
async def test_r9_backend_gagal_tetap_tombstone_dan_cancel(monkeypatch):
    calls: dict = {}

    async def _get(pool, **kw):
        return {"id": "doc-1"}

    async def _tomb(pool, **kw):
        calls["tombstone"] = True
        return True

    async def _cancel(pool, **kw):
        calls["cancel"] = True
        return 2

    class _PgBoom:
        def __init__(self, *a, **k):
            calls["pg"] = True

        async def delete_document(self, **kw):
            raise ConnectionError("pg down")

    class _GraphOk:
        def __init__(self, *a, **k):
            calls["graph"] = True

        async def delete_subgraph(self, **kw):
            return (3, 5)

    monkeypatch.setattr(repo, "get_document", _get)
    monkeypatch.setattr(repo, "tombstone_document", _tomb)
    monkeypatch.setattr(repo, "cancel_jobs_for_document", _cancel)
    monkeypatch.setattr("app.infra.rag.pgvector.PgVectorStore", _PgBoom)
    monkeypatch.setattr("app.infra.rag.pg_graph.PgGraphStore", _GraphOk)
    out = await _ctrl().delete(workspace="toko-a", document_id="doc-1")
    assert out["ok"] is True
    assert out["errors"] == ["pg: ConnectionError"]
    assert out["backends"]["graph_entities"] == 3
    assert calls.get("tombstone") is True and calls.get("cancel") is True


@pytest.mark.asyncio
async def test_r9_qdrant_gagal_graph_tetap_jalan(monkeypatch):
    from types import SimpleNamespace

    calls: dict = {}

    async def _get(pool, **kw):
        return {"id": "doc-1"}

    async def _tomb(pool, **kw):
        return True

    async def _cancel(pool, **kw):
        return 0

    class _PgOk:
        def __init__(self, *a, **k):
            pass

        async def delete_document(self, **kw):
            return 4

    class _QBoom:
        def __init__(self, *a, **k):
            pass

        async def delete_document(self, **kw):
            raise TimeoutError("qdrant lambat")

    class _GraphOk:
        def __init__(self, *a, **k):
            calls["graph"] = True

        async def delete_subgraph(self, **kw):
            return (1, 1)

    monkeypatch.setattr(repo, "get_document", _get)
    monkeypatch.setattr(repo, "tombstone_document", _tomb)
    monkeypatch.setattr(repo, "cancel_jobs_for_document", _cancel)
    monkeypatch.setattr("app.infra.rag.pgvector.PgVectorStore", _PgOk)
    monkeypatch.setattr("app.infra.rag.qdrant.QdrantStore", _QBoom)
    monkeypatch.setattr("app.infra.rag.pg_graph.PgGraphStore", _GraphOk)
    ctrl = DocumentController(_settings(), pool=None,
                              rag_clients=SimpleNamespace(qdrant=object(),
                                                          dims=1024))
    out = await ctrl.delete(workspace="toko-a", document_id="doc-1")
    assert out["ok"] is True
    assert out["errors"] == ["qdrant: TimeoutError"]
    assert out["backends"]["pg"] == 4
    assert calls.get("graph") is True


@pytest.mark.asyncio
async def test_r9_dokumen_tak_ada_ok_false_tanpa_side_effect(monkeypatch):
    calls: dict = {}

    async def _get(pool, **kw):
        return None

    class _PgJangan:
        def __init__(self, *a, **k):
            calls["pg"] = True

        async def delete_document(self, **kw):
            return 1

    monkeypatch.setattr(repo, "get_document", _get)
    monkeypatch.setattr("app.infra.rag.pgvector.PgVectorStore", _PgJangan)
    out = await _ctrl().delete(workspace="toko-a", document_id="tak-ada")
    assert out == {"ok": False}
    assert "pg" not in calls


# --- R10: reuse-or-create dalam satu transaksi terkunci ---

class _Cur:
    def __init__(self, pool, row):
        self._pool = pool
        self._row = row

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, pool, script):
        self._pool = pool
        self._script = list(script)

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
        self._pool.sql.append(" ".join(str(sql).split()))
        if str(sql).startswith("SELECT set_config"):
            return _Cur(self._pool, None)
        return _Cur(self._pool, self._script.pop(0))


class _Pool:
    def __init__(self, script):
        self._script = script
        self.sql: list[str] = []
        self.tx = 0
        self.conns = 0

    def connection(self):
        self.conns += 1
        script, pool = self._script, self

        class _Ctx:
            async def __aenter__(self):
                return _Conn(pool, script)

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_r10_reuse_job_hidup_tanpa_insert():
    live = {"id": 5, "state": "queued", "document_id": "d1"}
    pool = _Pool([{"id": "d1"}, live])  # lock-row, select-job
    job, created = await repo.reuse_or_create_job(
        pool, workspace="toko-a", document_id="d1")
    assert (job, created) == (live, False)
    assert pool.conns == 1 and pool.tx == 1  # satu transaksi
    assert "FOR UPDATE" in pool.sql[1]  # kunci DULU sebelum baca job
    assert not any(s.startswith("INSERT") for s in pool.sql)


@pytest.mark.asyncio
async def test_r10_tanpa_job_atau_dead_buat_baru():
    for job_row in (None, {"id": 5, "state": "dead", "document_id": "d1"}):
        pool = _Pool([{"id": "d1"}, job_row, {"id": 9, "state": "queued"}])
        job, created = await repo.reuse_or_create_job(
            pool, workspace="toko-a", document_id="d1")
        assert (job["id"], created) == (9, True)
        assert any(s.startswith("INSERT") for s in pool.sql)


@pytest.mark.asyncio
async def test_r10_done_dipakai_ulang_bukan_diproses_ulang():
    done = {"id": 5, "state": "done", "document_id": "d1"}
    pool = _Pool([{"id": "d1"}, done])
    job, created = await repo.reuse_or_create_job(
        pool, workspace="toko-a", document_id="d1")
    assert (job, created) == (done, False)
