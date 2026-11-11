"""Reconciler PG → Qdrant (D4/T10): `python -m app.workers.reindex`.

PG adalah SoR; Qdrant turunan rebuildable. Per tenant:
- bandingkan chunk PG vs point Qdrant (by point_id deterministik),
- upsert yang hilang + hapus orphan,
- laporkan selisih (kriteria cutover: selisih = 0).

Dipakai: backfill awal, pasca-migrasi backend, perbaikan berkala (cron).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

log = logging.getLogger("app.workers.reindex")


def parse_vector(raw: object) -> list[float]:
    """Parse literal pgvector '[0.1,...]' → list float (tanpa dep pgvector)."""
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    if not isinstance(raw, str):
        raise ValueError(f"embedding bentuk tak dikenal: {type(raw)}")
    s = raw.strip()
    if not (s.startswith("[") and s.endswith("]")):
        raise ValueError("literal vektor tak valid")
    return [float(x) for x in s[1:-1].split(",") if x.strip()]


async def reindex_tenant(pool, qdrant_client, collection: str, *,
                         tenant: str, since: str | None = None,
                         dry_run: bool = False,
                         batch: int = 100) -> dict:
    """Rekonsiliasi 1 tenant. Return laporan {pg, qdrant_sebelum, ...}."""
    from qdrant_client import models

    from app.infra.rag.qdrant import point_id

    pg_ids: dict[str, dict] = {}
    async with pool.connection() as conn:
        async with conn.transaction():
            # SET LOCAL: benar di bawah RLS FORCED maupun superuser (K2).
            await conn.execute(
                "SELECT set_config('app.current_tenant', %s, true)",
                (tenant,))
            params: list = [tenant]
            filt = ""
            if since:
                filt = " AND c.created_at >= %s::timestamptz"
                params.append(since)
            # Keyset pagination (hindani fetchall raksasa, P1).
            last_doc, last_ord = "00000000-0000-0000-0000-000000000000", -1
            while True:
                cur = await conn.execute(
                    "SELECT c.document_id, c.ord, c.content, c.page, c.sheet,"
                    " c.heading, c.token_count, c.meta,"
                    " c.embedding::text AS emb,"
                    " c.embedding_model, c.embedding_dim, c.embedding_version,"
                    " d.title AS source FROM chunks c"
                    " JOIN documents d ON d.id = c.document_id"
                    " WHERE c.tenant = %s" + filt +
                    " AND (c.document_id, c.ord) > (%s::uuid, %s)"
                    " ORDER BY c.document_id, c.ord LIMIT 2000",
                    params + [last_doc, last_ord])
                rows = await cur.fetchall()
                if not rows:
                    break
                for r in rows:
                    pid = point_id(tenant, str(r["document_id"]),
                                   int(r["ord"]))
                    pg_ids[pid] = dict(r)
                last_doc = str(rows[-1]["document_id"])
                last_ord = int(rows[-1]["ord"])

    # 2) Sisi Qdrant: semua point tenant (scroll paginal).
    filt = models.Filter(must=[models.FieldCondition(
        key="tenant", match=models.MatchValue(value=tenant))])
    q_ids: set[str] = set()
    offset = None
    while True:
        points, offset = await qdrant_client.scroll(
            collection, scroll_filter=filt, limit=1000, offset=offset,
            with_payload=False, with_vectors=False)
        q_ids.update(str(p.id) for p in points)
        if offset is None:
            break

    # Guard anti-hapus-massal (K2): PG kosong + Qdrant berisi = filter/RLS
    # salah, BUKAN orphan — abort SEBELUM delete.
    if not pg_ids and q_ids and not dry_run and since is None:
        raise SystemExit(
            f"REFUSE {tenant}: pg=0 tapi qdrant={len(q_ids)} — curiga "
            "filter/RLS salah, abort sebelum delete.")

    missing = [pid for pid in pg_ids if pid not in q_ids]
    orphans = [pid for pid in q_ids if pid not in pg_ids]
    report = {"tenant": tenant, "pg": len(pg_ids),
              "qdrant_sebelum": len(q_ids), "hilang": len(missing),
              "orphan": len(orphans), "upserted": 0, "deleted": 0,
              "dry_run": dry_run}
    if dry_run:
        report["match"] = not missing and not orphans
        return report

    # 3) Perbaiki: upsert hilang (batch) + hapus orphan.
    for i in range(0, len(missing), batch):
        batch_ids = missing[i:i + batch]
        points = []
        for pid in batch_ids:
            r = pg_ids[pid]
            meta = dict(r["meta"] or {})
            points.append(models.PointStruct(
                id=pid, vector=parse_vector(r["emb"]),
                payload={"tenant": tenant,
                         "document_id": str(r["document_id"]),
                         "ord": int(r["ord"]), "content": r["content"],
                         "page": r["page"], "sheet": r["sheet"],
                         "heading": r["heading"],
                         "token_count": r["token_count"],
                         "source": r["source"] or "", "meta": meta,
                         "embedding_model": r["embedding_model"],
                         "embedding_dim": r["embedding_dim"],
                         "embedding_version": r["embedding_version"]}))
        await qdrant_client.upsert(collection, points, wait=True)
        report["upserted"] += len(points)
    if orphans and since is None:
        # Mode --since = inkremental: JANGAN hapus orphan (tak sebanding).
        for i in range(0, len(orphans), batch):
            await qdrant_client.delete(
                collection, points_selector=models.PointIdsList(
                    points=orphans[i:i + batch]), wait=True)
            report["deleted"] += len(orphans[i:i + batch])
    report["match"] = (report["upserted"] == len(missing)
                       and (report["deleted"] == len(orphans)
                            or since is not None))
    return report


async def _amain(args) -> int:
    from app.core.config import get_settings
    from app.infra.lifespan import ensure_selector_loop
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag_migrate import apply_rag_migration

    ensure_selector_loop()
    settings = get_settings()
    if not (settings.QDRANT_URL or "").strip() or not settings.QDRANT_API_KEY:
        print("QDRANT_URL/QDRANT_API_KEY kosong — isi dulu.", file=sys.stderr)
        return 2
    from qdrant_client import AsyncQdrantClient

    from app.infra.rag.qdrant import QdrantStore, collection_name

    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await apply_rag_migration(pool)
        # Dimensi dari DATA PG (SoR), bukan probe embed (P1: reindex jalan
        # tanpa service embed; init_rag = side-effect + butuh tokenizer).
        dims = await _pg_dims(pool)
        collection = collection_name(dims, settings.RAG_EMBED_MODEL)
        client = AsyncQdrantClient(url=settings.QDRANT_URL,
                                   api_key=settings.QDRANT_API_KEY, timeout=30)
        try:
            probe = QdrantStore(client, collection, tenant="__probe__")
            await probe.ensure_collection()
            tenants = [args.tenant] if args.tenant else await _all_tenants(pool)
            ok_all = True
            for tenant in tenants:
                rep = await reindex_tenant(
                    pool, client, collection, tenant=tenant,
                    since=args.since, dry_run=args.dry_run)
                print(f"{tenant}: pg={rep['pg']}"
                      f" qdrant={rep['qdrant_sebelum']} upserted={rep['upserted']}"
                      f" deleted={rep['deleted']} match={rep['match']}")
                ok_all = ok_all and rep["match"]
            return 0 if ok_all else 1
        finally:
            await client.close()
    finally:
        await close_pool(pool)


async def _pg_dims(pool) -> int:
    """Dimensi dari triple dominan di PG. Campur = abort jujur (D6)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT embedding_dim, COUNT(*) AS n FROM chunks"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 2")
        rows = await cur.fetchall()
    if not rows:
        return 1024  # index kosong: koleksi dibuat dengan default pin
    if len(rows) > 1:
        raise SystemExit(
            "REFUSE: chunks campur dimensi "
            f"{[(r['embedding_dim'], r['n']) for r in rows]} — rapikan dulu.")
    return int(rows[0]["embedding_dim"])


async def _all_tenants(pool) -> list[str]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT DISTINCT tenant FROM chunks")
        return [r["tenant"] for r in await cur.fetchall()]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Reconciler PG → Qdrant (D4).")
    ap.add_argument("--tenant", default="",
                    help="tenant saja (kosong = semua)")
    ap.add_argument("--since", default=None,
                    help="ISO timestamp (inkremental, tanpa hapus orphan)")
    ap.add_argument("--dry-run", action="store_true",
                    help="lapor saja, tanpa tulis")
    args = ap.parse_args(argv)
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
