"""Live PgGraphStore: upsert/expand/hapus/isolasi (DB nyata, tanpa LLM)."""

from __future__ import annotations

import hashlib
import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

from app.infra.postgres import close_pool, create_pool, open_pool  # noqa: E402
from app.infra.rag.pg_graph import PgGraphStore  # noqa: E402
from app.infra.rag_migrate import apply_rag_migration  # noqa: E402


@pytest_asyncio.fixture
async def stores(db_url, tag):
    from app.core.config import Settings

    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5)
    pool = create_pool(settings)
    await open_pool(pool)
    await apply_rag_migration(pool)
    tenants = {s: f"it-g08-{s}-{tag}" for s in ("a", "b")}
    ctx = {"pool": pool,
           "a": PgGraphStore(pool, tenant=tenants["a"]),
           "b": PgGraphStore(pool, tenant=tenants["b"])}
    yield ctx
    try:
        async with pool.connection() as conn:
            for t in tenants.values():
                await conn.execute(
                    "DELETE FROM kg_edges WHERE tenant = %s", (t,))
                await conn.execute(
                    "DELETE FROM kg_entities WHERE tenant = %s", (t,))
                await conn.execute(
                    "DELETE FROM documents WHERE tenant = %s", (t,))
    finally:
        await close_pool(pool)


async def _doc(pool, tenant: str, title: str) -> str:
    doc_id = str(uuid.uuid4())
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO documents (id, tenant, source_type, source_uri,"
            " title, bytes, sha256, status)"
            " VALUES (%s, %s, 'txt', %s, %s, 0, %s, 'ready')",
            (doc_id, tenant, f"test://{doc_id}", title,
             hashlib.sha256(doc_id.encode()).hexdigest()))
    return doc_id


def _ent(name, type="produk", conf=0.9, ord=0):
    from app.rag.normalize import normalize_entity
    return {"name": name, "norm": normalize_entity(name), "type": type,
            "confidence": conf, "chunk_ord": ord, "chunk_id": f"c{ord}"}


def _edge(src, dst, rel="memasok", conf=0.9, ord=0, acl="public"):
    from app.rag.normalize import normalize_entity
    return {"src_norm": normalize_entity(src),
            "dst_norm": normalize_entity(dst), "relation": rel,
            "confidence": conf, "layer": "L2", "coref_resolved": True,
            "access_level": acl, "chunk_ord": ord, "chunk_id": f"c{ord}",
            "chunk_row_id": None}


@pytest.mark.asyncio
async def test_upsert_dan_expand_1hop(stores):
    pool = stores["pool"]
    doc = await _doc(pool, stores["a"]._tenant, "pemasok.md")
    n_ent, n_edge = await stores["a"].upsert_subgraph(
        doc_id=doc,
        entities=[_ent("Kopi Arabika"), _ent("PT Sumber Rasa", "organisasi")],
        edges=[_edge("PT Sumber Rasa", "Kopi Arabika")])
    assert (n_ent, n_edge) == (2, 1)
    hits = await stores["a"].expand(["pt sumber rasa"], k=5)
    assert len(hits) == 1
    h = hits[0]
    assert h["hops"] == 1 and h["doc_id"] == doc
    assert "memasok" in h["content"]
    assert h["metadata"]["kind"] == "graph"
    assert h["metadata"]["provenance"] == ["c0"]
    assert h["chunk_id"]  # gpath stabil (32 hex)


@pytest.mark.asyncio
async def test_expand_2hop_dan_stitch_lintas_dok(stores):
    pool = stores["pool"]
    t = stores["a"]._tenant
    d1 = await _doc(pool, t, "d1.md")
    d2 = await _doc(pool, t, "d2.md")
    await stores["a"].upsert_subgraph(
        doc_id=d1, entities=[_ent("Kopi Arabika"), _ent("PT Sumber Rasa")],
        edges=[_edge("PT Sumber Rasa", "Kopi Arabika")])
    await stores["a"].upsert_subgraph(
        doc_id=d2, entities=[_ent("PT Sumber Rasa"), _ent("Bandung", "lokasi")],
        edges=[_edge("PT Sumber Rasa", "Bandung",
                     rel="berlokasi_di", ord=1)])
    hits = await stores["a"].expand(["kopi arabika"], k=5, max_hops=2)
    two = [h for h in hits if h["hops"] == 2]
    assert two, [ (h["hops"], h["content"]) for h in hits ]
    assert "Bandung" in two[0]["content"]
    assert len(two[0]["metadata"]["provenance"]) == 2
    # max_hops=1 → tanpa path 2-hop.
    hits1 = await stores["a"].expand(["kopi arabika"], k=5, max_hops=1)
    assert all(h["hops"] == 1 for h in hits1)


@pytest.mark.asyncio
async def test_isolasi_tenant_silang(stores):
    pool = stores["pool"]
    doc = await _doc(pool, stores["b"]._tenant, "rahasia.md")
    await stores["b"].upsert_subgraph(
        doc_id=doc, entities=[_ent("Rahasia Dagang")],
        edges=[_edge("Rahasia Dagang", "X", rel="terkait_dengan")])
    assert await stores["a"].expand(["rahasia dagang"], k=5) == []
    info = await stores["a"].graph_info()
    assert info["edge_count"] == 0


@pytest.mark.asyncio
async def test_filter_access_level(stores):
    pool = stores["pool"]
    t = stores["a"]._tenant
    doc = await _doc(pool, t, " campur.md")
    await stores["a"].upsert_subgraph(
        doc_id=doc,
        entities=[_ent("Gaji"), _ent("Budi", "orang"), _ent("Kopi")],
        edges=[_edge("Budi", "Gaji", rel="memiliki", acl="internal"),
               _edge("Budi", "Kopi", rel="memiliki", ord=1)])
    pub = await stores["a"].expand(["budi"], k=5,
                                   filters={"access_level": "public"})
    assert [h["content"] for h in pub] == ["Budi memiliki Kopi."]
    internal = await stores["a"].expand(
        ["budi"], k=5, filters={"access_level": "internal"})
    assert len(internal) == 1 and "Gaji" in internal[0]["content"]
    with pytest.raises(ValueError, match="tak dikenal"):
        await stores["a"].expand(["budi"], k=5, filters={"doc_type": "x"})


@pytest.mark.asyncio
async def test_hapus_dan_idempoten(stores):
    pool = stores["pool"]
    t = stores["a"]._tenant
    doc = await _doc(pool, t, "hapus.md")
    await stores["a"].upsert_subgraph(
        doc_id=doc, entities=[_ent("A"), _ent("B")],
        edges=[_edge("A", "B", rel="terkait_dengan")])
    # Upsert ulang = hasil sama (tanpa ganda).
    n1 = await stores["a"].upsert_subgraph(
        doc_id=doc, entities=[_ent("A"), _ent("B")],
        edges=[_edge("A", "B", rel="terkait_dengan")])
    assert n1 == (2, 1)
    info = await stores["a"].graph_info()
    assert (info["entity_count"], info["edge_count"]) == (2, 1)
    assert info["extractor"] and info["onto_version"] == "kg_onto/v1"
    assert await stores["a"].delete_subgraph(doc_id=doc) == (2, 1)
    assert await stores["a"].delete_subgraph(doc_id=doc) == (0, 0)
    assert await stores["a"].expand(["a"], k=5) == []


@pytest.mark.asyncio
async def test_dedup_intra_batch_last_wins(stores):
    """Batch ganda (entity/edge sama 2x) = 1 baris, bukan crash 21000."""
    pool = stores["pool"]
    t = stores["a"]._tenant
    doc = await _doc(pool, t, "dedup.md")
    e1 = _ent("Ganda")
    e2 = _ent("Ganda")
    g1 = _edge("Ganda", "Target", rel="terkait_dengan")
    g2 = _edge("Ganda", "Target", rel="terkait_dengan", conf=0.5)
    n_ent, n_edge = await stores["a"].upsert_subgraph(
        doc_id=doc, entities=[e1, e2, _ent("Target")], edges=[g1, g2])
    assert (n_ent, n_edge) == (2, 1)
    info = await stores["a"].graph_info()
    assert (info["entity_count"], info["edge_count"]) == (2, 1)


@pytest.mark.asyncio
async def test_validasi_dan_seed_kosong(stores):
    pool = stores["pool"]
    t = stores["a"]._tenant
    doc = await _doc(pool, t, "valid.md")
    with pytest.raises(ValueError, match="relasi tak dikenal"):
        await stores["a"].upsert_subgraph(
            doc_id=doc, entities=[_ent("A")], edges=[_edge("A", "B", rel="x")])
    with pytest.raises(ValueError, match="self-loop"):
        await stores["a"].upsert_subgraph(
            doc_id=doc, entities=[_ent("A")],
            edges=[_edge("A", "A", rel="terkait_dengan")])
    with pytest.raises(ValueError, match="tipe node"):
        await stores["a"].upsert_subgraph(
            doc_id=doc, entities=[_ent("A", type="alien")], edges=[])
    bad = _edge("A", "B", rel="terkait_dengan")
    bad["chunk_id"] = ""
    with pytest.raises(ValueError, match="chunk_id"):
        await stores["a"].upsert_subgraph(
            doc_id=doc, entities=[_ent("A")], edges=[bad])
    bad2 = _edge("A", "B", rel="terkait_dengan")
    bad2["layer"] = "L9"
    with pytest.raises(ValueError, match="layer"):
        await stores["a"].upsert_subgraph(
            doc_id=doc, entities=[_ent("A")], edges=[bad2])
    bad3 = _edge("A", "B", rel="terkait_dengan")
    bad3["confidence"] = 9.9
    with pytest.raises(ValueError, match="confidence"):
        await stores["a"].upsert_subgraph(
            doc_id=doc, entities=[_ent("A")], edges=[bad3])
    assert await stores["a"].expand([], k=5) == []
    assert await stores["a"].expand(["   "], k=5) == []
    with pytest.raises(ValueError, match="wajib tenant"):
        PgGraphStore(pool, tenant="")
    with pytest.raises(ValueError, match="≥ 1"):
        await stores["a"].expand(["a"], k=0)
    with pytest.raises(ValueError, match="max_hops"):
        await stores["a"].expand(["a"], k=5, max_hops=3)
    with pytest.raises(ValueError, match="fanout"):
        await stores["a"].expand(["a"], k=5, fanout=0)
