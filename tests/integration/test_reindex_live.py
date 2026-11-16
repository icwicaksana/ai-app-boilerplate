"""Live reconciler: seed PG → reindex → Qdrant cocok + orphan dibersihkan."""

import hashlib
import uuid

import pytest

from app.workers.reindex import parse_vector  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_reindex_cocok_dan_bersihkan_orphan(db_url, tag):
    import os

    from qdrant_client import AsyncQdrantClient, models

    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.pgvector import PgVectorStore
    from app.infra.rag.qdrant import QdrantStore, collection_name
    from app.infra.rag_migrate import apply_rag_migration
    from app.workers.reindex import reindex_workspace

    url = (os.environ.get("TEST_QDRANT_URL")
           or os.environ.get("QDRANT_URL", "http://localhost:6333")).strip()
    key = (os.environ.get("TEST_QDRANT_API_KEY")
           or os.environ.get("QDRANT_API_KEY", "")).strip()
    if not key:
        pytest.skip("QDRANT_API_KEY kosong")
    client = AsyncQdrantClient(url=url, api_key=key, timeout=15)
    try:
        await client.get_collections()
    except Exception:
        pytest.skip("Qdrant tak terjangkau")

    workspace = f"it-rx-{tag}"
    collection = f"rx_{tag}_{collection_name(1024, 'fake-model')}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5)
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await apply_rag_migration(pool)
        qstore = QdrantStore(client, collection, workspace=workspace,
                             model="fake-model", dim=1024, version="t")
        await qstore.ensure_collection()

        # Seed PG: 1 dokumen 2 chunk (1024-dim agar lolos CHECK dimensi).
        import random

        def _vec(seed_text: str) -> list[float]:
            rnd = random.Random(int(hashlib.sha256(
                seed_text.encode()).hexdigest(), 16) % (2 ** 31))
            v = [rnd.gauss(0.0, 1.0) for _ in range(1024)]
            n = sum(x * x for x in v) ** 0.5
            return [x / n for x in v]

        doc_id = str(uuid.uuid4())
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant', %s, true)",
                    (workspace,))
                await conn.execute(
                    "INSERT INTO documents (id, workspace, source_type,"
                    " source_uri, title, bytes, sha256, status)"
                    " VALUES (%s, %s, 'txt', 'test://x', 'rx.txt', 3,"
                    " %s, 'ready')",
                    (doc_id, workspace,
                     hashlib.sha256(doc_id.encode()).hexdigest()))
        pg = PgVectorStore(pool, workspace=workspace, model="fake-model", dim=1024,
                           version="t")
        vecs = [_vec("rx-0"), _vec("rx-1")]
        await pg.upsert_chunks(doc_id=doc_id, chunks=[
            {"content": f"chunk rx {i}", "vector": vecs[i], "ord": i,
             "token_count": 3, "metadata": {}} for i in range(2)])

        # Orphan di Qdrant (tak ada di PG).
        await client.upsert(collection, [models.PointStruct(
            id="00000000-0000-0000-0000-00000000ffff",
            vector=[0.0] * 1024,
            payload={"workspace": workspace, "document_id": doc_id, "ord": 99,
                     "content": "orphan"})], wait=True)

        rep = await reindex_workspace(pool, client, collection, workspace=workspace)
        assert rep["pg"] == 2, rep
        assert rep["upserted"] == 2, rep
        assert rep["deleted"] == 1, rep
        assert rep["match"] is True

        rep2 = await reindex_workspace(pool, client, collection, workspace=workspace,
                                    dry_run=True)
        assert rep2["match"] is True and rep2["upserted"] == 0

        hits = await qstore.search_dense(vecs[0], k=5)
        assert hits and hits[0]["content"] == "chunk rx 0"
    finally:
        try:
            await client.delete_collection(collection)
        finally:
            await client.close()
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace = %s", (workspace,))
        finally:
            await close_pool(pool)


def test_parse_vector_unit():
    assert parse_vector([1, 2]) == [1.0, 2.0]
    assert parse_vector("[0.5,-1.25]") == [0.5, -1.25]
    with pytest.raises(ValueError):
        parse_vector("bukan-vektor")
    with pytest.raises(ValueError):
        parse_vector(None)
