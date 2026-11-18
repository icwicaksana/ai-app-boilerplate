"""v1.0 V1-D/1: ACL graph benar (G1 SoR + G2 fetch-filter + 021)."""

from __future__ import annotations

import pytest

from app.infra.rag.pg_graph import PgGraphStore


@pytest.mark.asyncio
async def test_g1_extractor_tak_emit_access_level():
    """Kontrak: output ekstraktor TANPA kunci access_level (SoR = chunk)."""
    from app.infra.rag.extract.l1_rules import L1RulesExtractor
    from app.infra.rag.extract.stub import StubExtractor

    text = "PT Sumber Rasa memasok Kopi Arabika."
    for ext in (L1RulesExtractor(), StubExtractor()):
        out = await ext.extract([text])
        assert out[0]["edges"], type(ext).__name__
        for e in out[0]["edges"]:
            assert "access_level" not in e, (type(ext).__name__, e)


@pytest.mark.asyncio
async def test_g1_worker_timpa_dengan_acl_chunk(monkeypatch):
    """Inti G1: edge hostile 'public' di chunk internal → tertulis internal."""
    from types import SimpleNamespace

    import app.workers.ingest as ingest
    from tests._auth_helpers import test_settings

    captured: dict = {}

    class _Store:
        def __init__(self, *a, **k):
            pass

        async def upsert_subgraph(self, *, doc_id, entities, edges):
            captured["edges"] = edges
            return (len(entities), len(edges))

    class _Cur:
        async def fetchall(self):
            return [{"ord": 0, "id": "r0"}]

    class _Conn:
        async def execute(self, *a, **k):
            return _Cur()

        def transaction(self):
            class _Tx:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *a):
                    return False

            return _Tx()

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Ctx()

    class _Nakal:
        model = "nakal"
        version = "v0"

        async def extract(self, texts):
            return [{"entities": [], "edges": [{
                "src_norm": "pt a", "dst_norm": "kopi b",
                "relation": "memasok", "confidence": 1.0, "layer": "L2",
                "coref_resolved": True,
                "access_level": "public",  # hostile: kontrakan SoR
            }]} for _ in texts]

    monkeypatch.setattr("app.infra.rag.pg_graph.PgGraphStore", _Store)
    settings = test_settings(GRAPH_ENABLED=True)
    ctx = SimpleNamespace(settings=settings, pool=_Pool(), extractor=_Nakal(),
                          cache=None)

    async def _stage(name, pct):
        return None

    rows = [{"content": "x", "ord": 0,
             "metadata": {"access_level": "internal",
                          "_chunk_id": "c0"}}]
    import app.workers.ingest as _ing  # modul sudah diimpor di atas

    await _ing._run_graph_stage(ctx, workspace="toko-a", job_id=1,
                                doc_id="d0", rows=rows, stage_fn=_stage)
    assert captured["edges"][0]["access_level"] == "internal"


@pytest.mark.asyncio
async def test_g2_fetch_terfilter_acl():
    """_fetch_* menyertakan COALESCE access_level saat acl diberikan."""

    class _Cur:
        def __init__(self):
            self.rows = []

        async def fetchall(self):
            return self.rows

    class _Conn:
        def __init__(self):
            self.sql: list[str] = []
            self.params: list = []

        async def execute(self, sql, params=None):
            self.sql.append(" ".join(str(sql).split()))
            self.params.append(params)
            return _Cur()

    store = PgGraphStore(pool=None, workspace="toko-a")
    conn = _Conn()
    await store._fetch_contents(conn, {"r1"}, acl=["internal"])
    assert "COALESCE(meta->>'access_level', 'public') = ANY(%s)" in conn.sql[0]
    assert conn.params[0][-1] == ["internal"]
    conn2 = _Conn()
    await store._fetch_titles(conn2, {"d1"}, acl=["public"])
    assert "COALESCE(meta->>'access_level', 'public') = ANY(%s)" in conn2.sql[0]


def test_g2_hop_tak_lolos_gugurkan_path():
    """Provenance tersaring → SELURUH path gugur (bukan konten kosong)."""
    store = PgGraphStore(pool=None, workspace="toko-a")
    paths = [{"edges": [
        {"id": 1, "src_norm": "a", "dst_norm": "b", "relation": "memasok",
         "confidence": 1.0, "layer": "L2", "document_id": "d1",
         "chunk_id": "c1", "chunk_row_id": "r1"},
        {"id": 2, "src_norm": "b", "dst_norm": "c", "relation": "memasok",
         "confidence": 1.0, "layer": "L2", "document_id": "d1",
         "chunk_id": "c2", "chunk_row_id": "r2"}],
        "nodes": ["a", "b", "c"], "score": 0.9}]
    names = {"a": "A", "b": "B", "c": "C"}
    titles = {"d1": "Dok"}
    full = {"r1": "isi-1", "r2": "isi-2"}
    # Lengkap → lolos.
    assert len(store._to_hits(paths, names, titles, full, k=5,
                              require_acl=True)) == 1
    # Satu hop tersaring (chunk internal) → path gugur.
    assert store._to_hits(paths, names, titles, {"r1": "isi-1"}, k=5,
                          require_acl=True) == []
    # Tanpa filter (internal/privileged) → perilaku lama (kosong diam).
    hits = store._to_hits(paths, names, titles, {"r1": "isi-1"}, k=5)
    assert len(hits) == 1
    assert hits[0]["metadata"]["provenance_contents"] == ["isi-1", ""]


def test_021_terdaftar_dengan_rollback():
    """Migrasi backfill ACL terdaftar + rollback eksak tersedia."""
    from pathlib import Path

    from app.infra.rag_migrate import MIGRATIONS, MIGRATIONS_DIR

    assert ("021", "021_backfill_kg_edge_acl.sql") in MIGRATIONS
    up = Path(MIGRATIONS_DIR) / "021_backfill_kg_edge_acl.sql"
    down = Path(MIGRATIONS_DIR) / "021_backfill_kg_edge_acl.rollback.sql"
    assert up.exists() and down.exists()
    text = up.read_text(encoding="utf-8")
    assert "FORCE RLS" in text and "BYPASSRLS" in text  # anti-gagal-senyap
    assert "chunk_row_id IS NULL" in text  # dua jalur backfill
