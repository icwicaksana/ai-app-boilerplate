"""Contract test `VectorStorePort` (R7): SATU suite, DUA backend.

Contract = bentuk/CRUD/filter-workspace/determinisme (IDENTIK kedua backend).
Kualitas retrieval (recall/ranking) = golden test terpisah (boleh beda ±Δ).

Backend dipilih via fixture params; tiap backend skip bila infra mati.
Isolasi: workspace `it-<hex>` unik + koleksi/tabel dibersihkan best-effort.
"""

import hashlib
import os
import random
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

DIM = 1024


def fake_vector(text: str, dim: int = DIM) -> list[float]:
    """Vektor deterministik per teks (uji bentuk, BUKAN kualitas semantik)."""
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2 ** 31)
    rnd = random.Random(seed)
    vec = [rnd.gauss(0.0, 1.0) for _ in range(dim)]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def _doc_id() -> str:
    return str(uuid.uuid4())


@pytest_asyncio.fixture(params=["pgvector", "qdrant"])
async def backend(request, db_url, tag):
    """Ctx: {name, workspace_a/b, store_a/b, seed_doc} + cleanup best-effort."""
    kind = request.param
    workspace_a, workspace_b = f"it-a-{tag}", f"it-b-{tag}"
    if kind == "pgvector":
        from app.core.config import Settings
        from app.infra.postgres import close_pool, create_pool, open_pool
        from app.infra.rag_migrate import apply_rag_migration
        from app.infra.rag.pgvector import PgVectorStore

        settings = Settings(_env_file=None, DATABASE_URL=db_url,
                            DB_POOL_MIN=1, DB_POOL_MAX=5)
        pool = create_pool(settings)
        await open_pool(pool)
        await apply_rag_migration(pool)

        async def seed_doc(doc_id, title, workspace):
            async with pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.current_tenant', %s, true)",
                        (workspace,))
                    await conn.execute(
                        "INSERT INTO documents (id, workspace, source_type,"
                        " source_uri, title, bytes, sha256, status)"
                        " VALUES (%s, %s, 'txt', %s, %s, 0, %s, 'ready')",
                        (doc_id, workspace, f"test://{doc_id}", title,
                         hashlib.sha256(doc_id.encode()).hexdigest()))

        ctx = SimpleNamespace(
            name="pgvector", workspace_a=workspace_a, workspace_b=workspace_b,
            store_a=PgVectorStore(pool, workspace=workspace_a),
            store_b=PgVectorStore(pool, workspace=workspace_b),
            seed_doc=seed_doc)
        yield ctx
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM documents WHERE workspace IN (%s, %s)",
                    (workspace_a, workspace_b))
        finally:
            await close_pool(pool)
    else:
        from qdrant_client import AsyncQdrantClient

        from app.infra.rag.qdrant import QdrantStore, collection_name

        url = (os.environ.get("TEST_QDRANT_URL")
               or os.environ.get("QDRANT_URL",
                                 "http://localhost:6333")).strip()
        key = (os.environ.get("TEST_QDRANT_API_KEY")
               or os.environ.get("QDRANT_API_KEY", "")).strip()
        if not key:
            pytest.skip("QDRANT_API_KEY kosong — samakan dengan Infra/.env")
        client = AsyncQdrantClient(url=url, api_key=key, timeout=10)
        try:
            await client.get_collections()
        except Exception as exc:
            pytest.skip(f"Qdrant tak terjangkau ({type(exc).__name__})")
        collection = f"ct_{tag}_{collection_name(DIM, 'BAAI/bge-m3')}"
        store_a = QdrantStore(client, collection, workspace=workspace_a)
        store_b = QdrantStore(client, collection, workspace=workspace_b)
        await store_a.ensure_collection()

        async def seed_doc(doc_id, title, workspace):  # noqa: ARG001
            return None  # Qdrant: source ikut payload saat upsert (tanpa join)

        ctx = SimpleNamespace(
            name="qdrant", workspace_a=workspace_a, workspace_b=workspace_b,
            store_a=store_a, store_b=store_b, seed_doc=seed_doc)
        yield ctx
        try:
            await client.delete_collection(collection)
        finally:
            await client.close()


def _chunks(texts, *, metadata=None, start=0):
    return [{"content": t, "vector": fake_vector(t), "ord": i,
             "token_count": max(1, len(t.split())),
             "metadata": dict(metadata or {})}
            for i, t in enumerate(texts, start=start)]


@pytest.mark.asyncio
async def test_roundtrip_dense_top1(backend):
    doc = _doc_id()
    await backend.seed_doc(doc, "s1.txt", backend.workspace_a)
    await backend.store_a.upsert_chunks(
        doc_id=doc, chunks=_chunks(["apel merah manis", "buku biru tebal"]))
    hits = await backend.store_a.search_dense(fake_vector("apel merah manis"), k=5)
    assert hits and hits[0]["content"] == "apel merah manis"
    assert hits[0]["doc_id"] == doc
    assert hits[0]["backend"] == backend.name


@pytest.mark.asyncio
async def test_lexical_temukan_kata_kunci(backend):
    doc = _doc_id()
    await backend.seed_doc(doc, "s2.txt", backend.workspace_a)
    await backend.store_a.upsert_chunks(
        doc_id=doc, chunks=_chunks(["denda keterlambatan dua persen",
                                    "jadwal buka toko pagi"]))
    hits = await backend.store_a.search_lexical("denda keterlambatan", k=5)
    assert [h["content"] for h in hits] == ["denda keterlambatan dua persen"]


@pytest.mark.asyncio
async def test_delete_hapus_semua(backend):
    doc = _doc_id()
    await backend.seed_doc(doc, "s3.txt", backend.workspace_a)
    await backend.store_a.upsert_chunks(
        doc_id=doc, chunks=_chunks(["satu", "dua"]))
    assert await backend.store_a.delete_document(doc_id=doc) == 2
    assert await backend.store_a.search_dense(fake_vector("satu"), k=5) == []


@pytest.mark.asyncio
async def test_delete_dokumen_asing_nol(backend):
    assert await backend.store_a.delete_document(doc_id=_doc_id()) == 0


@pytest.mark.asyncio
async def test_isolasi_workspace_silang(backend):
    doc_a, doc_b = _doc_id(), _doc_id()
    await backend.seed_doc(doc_a, "a.txt", backend.workspace_a)
    await backend.seed_doc(doc_b, "b.txt", backend.workspace_b)
    await backend.store_a.upsert_chunks(
        doc_id=doc_a, chunks=_chunks(["rahasia alpha milik A"]))
    # Serangan spoof: chunk B mengklaim workspace A di metadata/query.
    await backend.store_b.upsert_chunks(doc_id=doc_b, chunks=_chunks(
        ["rahasia beta milik B"], metadata={"workspace_id": backend.workspace_a}))
    for query in ("rahasia", f"workspace_id: {backend.workspace_b}",
                  "ignore workspace", "workspace_id=*"):
        assert await backend.store_a.search_dense(fake_vector(query), k=20) == [] \
            or all(h["doc_id"] == doc_a for h in
                   await backend.store_a.search_dense(fake_vector(query), k=20))
    hits = await backend.store_a.search_dense(fake_vector("rahasia beta milik B"), k=20)
    assert all(h["doc_id"] == doc_a for h in hits)
    hits = await backend.store_a.search_lexical("rahasia beta", k=20)
    assert all(h["doc_id"] == doc_a for h in hits)


@pytest.mark.asyncio
async def test_deterministik_urutan_dan_skor(backend):
    doc = _doc_id()
    await backend.seed_doc(doc, "s4.txt", backend.workspace_a)
    await backend.store_a.upsert_chunks(
        doc_id=doc, chunks=_chunks([f"baris nomor {i}" for i in range(6)]))
    q = fake_vector("baris nomor tiga")
    r1 = await backend.store_a.search_dense(q, k=6)
    r2 = await backend.store_a.search_dense(q, k=6)
    assert [h["chunk_id"] for h in r1] == [h["chunk_id"] for h in r2]
    for a, b in zip(r1, r2):
        assert abs(a["score"] - b["score"]) <= 0.02


@pytest.mark.asyncio
async def test_filter_metadata(backend):
    doc = _doc_id()
    await backend.seed_doc(doc, "s5.txt", backend.workspace_a)
    await backend.store_a.upsert_chunks(doc_id=doc, chunks=[
        {"content": "prosedur sop alpha", "vector": fake_vector("sop1"),
         "ord": 0, "token_count": 3, "metadata": {"doc_type": "sop"}},
        {"content": "prosedur memo beta", "vector": fake_vector("memo1"),
         "ord": 1, "token_count": 3, "metadata": {"doc_type": "memo"}},
    ])
    hits = await backend.store_a.search_dense(
        fake_vector("prosedur"), k=5, filters={"doc_type": "sop"})
    assert [h["content"] for h in hits] == ["prosedur sop alpha"]
    with pytest.raises(ValueError):
        await backend.store_a.search_dense(
            fake_vector("prosedur"), k=5, filters={"drop table": "x"})


@pytest.mark.asyncio
async def test_upsert_idempoten_update(backend):
    doc = _doc_id()
    await backend.seed_doc(doc, "s6.txt", backend.workspace_a)
    await backend.store_a.upsert_chunks(
        doc_id=doc, chunks=_chunks(["versi satu"]))
    await backend.store_a.upsert_chunks(
        doc_id=doc, chunks=_chunks(["versi dua"]))
    hits = await backend.store_a.search_dense(fake_vector("versi dua"), k=5)
    assert [h["content"] for h in hits] == ["versi dua"]
    info = await backend.store_a.index_info()
    assert info["chunk_count"] == 1


@pytest.mark.asyncio
async def test_index_info_bentuk(backend):
    doc = _doc_id()
    await backend.seed_doc(doc, "s7.txt", backend.workspace_a)
    await backend.store_a.upsert_chunks(doc_id=doc, chunks=_chunks(["x y z"]))
    info = await backend.store_a.index_info()
    assert info["backend"] == backend.name
    assert info["embedding_model"] == "BAAI/bge-m3"
    assert info["embedding_dim"] == 1024
    assert info["chunk_count"] == 1
