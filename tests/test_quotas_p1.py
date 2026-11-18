"""v1.0 V1-D/5: determinisme BFS + kuota nyata + paritas reindex (P1)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_layered_budget_per_doc_lintas_batch():
    """begin_doc sekali → budget dibagi semua batch (bukan per-batch)."""
    from app.infra.rag.extract.layered import LayeredExtractor

    class _L1:
        async def extract(self, texts):
            return [{"entities": [], "edges": []} for _ in texts]

    class _L2:
        model = "m2"
        version = "v2"

        async def extract(self, texts):
            # Selalu menyerah (0 edge) → kandidat L3.
            return [{"entities": [], "edges": []} for _ in texts]

    class _L3:
        def __init__(self):
            self.calls = 0

        async def extract(self, texts):
            self.calls += len(texts)
            return [{"entities": [], "edges": []} for _ in texts]

    l3 = _L3()
    ext = LayeredExtractor(_L1(), _L2(), l3)
    assert ext.begin_doc(100) == 10  # min(20, 10%×100)
    batch = ["harga 50000 rupiah"] * 8  # sensitif → trigger
    await ext.extract(batch)
    await ext.extract(batch)
    assert l3.calls == 10  # total 2 batch ≤ budget dok (dulu: 2×1)
    assert ext._l3_left == 0


@pytest.mark.asyncio
async def test_layered_tanpa_begin_doc_fallback_lama():
    """Tanpa begin_doc: cap per-panggilan (kompatibel)."""
    from app.infra.rag.extract.layered import LayeredExtractor

    class _L1:
        async def extract(self, texts):
            return [{"entities": [], "edges": []} for _ in texts]

    class _L2:
        model = "m2"
        version = "v2"

        async def extract(self, texts):
            return [{"entities": [], "edges": []} for _ in texts]

    class _L3:
        def __init__(self):
            self.calls = 0

        async def extract(self, texts):
            self.calls += len(texts)
            return [{"entities": [], "edges": []} for _ in texts]

    l3 = _L3()
    ext = LayeredExtractor(_L1(), _L2(), l3)
    await ext.extract(["harga 5"] * 8)
    assert l3.calls == 1  # min(20, max(1, 10%×8))
    assert ext.last_stats["l3_calls"] == 1


@pytest.mark.asyncio
async def test_l3_kuota_valkey_bersama():
    """Kuota harian via INCR atomik (bukan counter per-proses)."""

    from app.core.errors import ExtractorError
    from app.infra.rag.extract.llm_l3 import LlmExtractor

    seen: list = []

    class _Cache:
        def __init__(self):
            self.n = 0

        async def incr_window(self, key, ttl):
            self.n += 1
            seen.append(key)
            return (self.n, 60)

        async def get_json(self, key):
            return None

        async def set_json(self, key, value, ttl=None):
            return None

    class _LLM:
        def with_structured_output(self, cls):
            class _S:
                async def ainvoke(self, prompt):
                    from app.infra.rag.extract.llm_l3 import _L3Result

                    return _L3Result(entities=[], relations=[])

            return _S()

    ext = LlmExtractor(_LLM(), model="m3", daily_quota=2, cache=_Cache())
    await ext.extract(["teks a", "teks b"])  # 2 slot ok
    assert len(seen) == 2 and seen[0].startswith("kg:l3quota:")
    with pytest.raises(ExtractorError, match="kuota"):
        await ext.extract(["teks c"])  # slot ke-3 ditolak


@pytest.mark.asyncio
async def test_l3_kuota_fallback_lokal_tanpa_cache():
    from app.core.errors import ExtractorError
    from app.infra.rag.extract.llm_l3 import LlmExtractor

    class _LLM:
        def with_structured_output(self, cls):
            class _S:
                async def ainvoke(self, prompt):
                    from app.infra.rag.extract.llm_l3 import _L3Result

                    return _L3Result(entities=[], relations=[])

            return _S()

    ext = LlmExtractor(_LLM(), model="m3", daily_quota=1)  # cache None
    await ext.extract(["a"])
    with pytest.raises(ExtractorError, match="kuota"):
        await ext.extract(["b"])


@pytest.mark.asyncio
async def test_worker_lewati_graph_saat_kuota_edge(monkeypatch):
    """Kuota edges tercapai → graph dilewati jujur (vektor tetap jalan)."""
    from types import SimpleNamespace

    import app.infra.ingest_repo as repo
    import app.workers.ingest as ingest
    from tests._auth_helpers import test_settings

    async def _count(pool, *, workspace):
        return 500001

    monkeypatch.setattr(repo, "count_kg_edges", _count)
    settings = test_settings(GRAPH_ENABLED=True,
                             GRAPH_QUOTA_EDGES_PER_WORKSPACE=500000)
    ctx = SimpleNamespace(settings=settings, pool=object(),
                          extractor=object(), cache=None)

    async def _stage(name, pct):
        return None

    out = await ingest._run_graph_stage(
        ctx, workspace="toko-a", job_id=1, doc_id="d0", rows=[{"x": 1}],
        stage_fn=_stage)
    assert out["graph_status"] == "quota"
    assert out["edge_count"] == 500001


@pytest.mark.asyncio
async def test_level_order_by_id():
    """BFS per-node deterministik (ORDER BY e.id, bukan LIMIT arbitrer)."""
    from app.infra.rag.pg_graph import PgGraphStore

    sqls: list = []

    class _Cur:
        async def fetchall(self):
            return []

    class _Conn:
        async def execute(self, sql, params=None):
            sqls.append(" ".join(str(sql).split()))
            return _Cur()

    store = PgGraphStore(pool=None, workspace="toko-a")
    await store._level(_Conn(), ["b", "a"], fanout=50, access_sql="",
                       access_params=[])
    assert len(sqls) == 2  # fwd + bwd
    assert all("ORDER BY e.id LIMIT 50" in s for s in sqls)


@pytest.mark.asyncio
async def test_reindex_paritas_norm_acl_null():
    """Reindex: content_norm + default ACL + lewati vektor-NULL + match."""
    from app.workers import reindex as R

    rows = [
        {"document_id": "d1", "ord": 0, "content": "Halo Dunia",
         "page": 1, "sheet": None, "heading": None, "token_count": 2,
         "meta": {}, "content_norm": "halo dunia", "emb": "[0.1,0.2]",
         "embedding_model": "m", "embedding_dim": 2,
         "embedding_version": "v1", "source": "s.pdf"},
        {"document_id": "d1", "ord": 1, "content": " RUSAK ",
         "page": 1, "sheet": None, "heading": None, "token_count": 1,
         "meta": {"access_level": "internal"}, "content_norm": "rusak",
         "emb": None,  # vektor NULL → lewati
         "embedding_model": "m", "embedding_dim": 2,
         "embedding_version": "v1", "source": "s.pdf"},
    ]

    class _Cur:
        async def fetchall(self):
            nonlocal rows
            out, rows = rows, []
            return out

    class _Conn:
        def transaction(self):
            class _Tx:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *a):
                    return False

            return _Tx()

        async def execute(self, *a, **k):
            return _Cur()

    class _PoolCtx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _PoolCtx()

    class _Qdrant:
        def __init__(self):
            self.upserted = []

        async def scroll(self, *a, **k):
            return ([], None)

        async def delete(self, *a, **k):
            return None

        async def upsert(self, collection, points, wait=True):
            self.upserted.extend(points)

    q = _Qdrant()
    rep = await R.reindex_workspace(_Pool(), q, "col", workspace="toko-a")
    assert rep["pg"] == 2 and rep["null_vector_skipped"] == 1
    assert rep["upserted"] == 1 and rep["match"] is True
    pay = q.upserted[0].payload
    assert pay["content_norm"] == "halo dunia"  # paritas norm
    assert pay["meta"]["access_level"] == "public"  # default ACL
