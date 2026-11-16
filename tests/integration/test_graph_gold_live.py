"""Runner gold-graph 28 v0.8 F5: retrieval-only, 6 sel ablation.

Praregistrasi: Test/spike_v08/prereg_f5.md (matriks + ambang, anti p-hacking).
Satu ingest (layered L1+L2, L3 mati), N pass retrieval per sel.
Gate keras: vguard canary + FPR=0 (T3). T1/T2/T4/T5/T7 dicatat → keputusan
kalibrasi pasca-run, lalu dikunci jadi assertion.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ARTIFACT = (Path(__file__).resolve().parents[2] / ".." / "Test" / "spike_v08")

PEMASOK = """## Jaringan Pemasok Toko Maju Jaya

PT Sumber Rasa memasok Kopi Arabika.
PT Sumber Rasa memasok Gula Aren.
Kopi Arabika dipakai di Menu Es Kopi Susu.
Gula Aren dipakai di Menu Es Kopi Susu.
Gula Aren dipakai di Menu Teh Manis.
PT Sumber Rasa berlokasi di Bandung.
PT Tiga Rasa memasok Teh Melati.
Teh Melati dipakai di Menu Teh Manis.
"""

TIM = """## Struktur Tim

Budi Hartono bekerja sebagai Manajer Operasional.
Siti Rahayu bekerja sebagai Staf Keuangan.
Manajer Operasional menerbitkan Dokumen SOP Retur.
PT Maju Jaya memiliki Divisi Logistik.
Dewi Lestari bekerja sebagai Kepala Divisi Logistik.
Laptop ThinkPad dipakai di Divisi Logistik.
"""

DOCS: dict[str, tuple[str, str, bytes]] = {
    "kebijakan": ("a", "kebijakan.md",
                  "## Kebijakan Toko Maju Jaya\n\nBarang bisa diretur maksimal "
                  "14 hari dengan nota pembelian.\n\nGaransi laptop 24 bulan "
                  "sejak tanggal beli.\n".encode()),
    "harga": ("a", "harga.md",
              "## Daftar Harga\n\n| Produk | Kemasan | Harga |\n"
              "|---|---|---|\n| Kopi Arabika | 200 gram | 50000 |\n"
              "| Teh Melati | 100 gram | 25000 |\n".encode()),
    "pemasok": ("a", "pemasok.md", PEMASOK.encode()),
    "tim": ("a", "tim.md", TIM.encode()),
    "promo": ("b", "promo.md",
              "## Promo Member\n\nDiskon member 10 persen setiap hari Jumat.\n"
              .encode()),
}

# (suf, stratum, question, expected_sources|None, need_graph)
GOLD: list[tuple[str, str, str, list[str] | None, bool]] = [
    # bridge (8): butuh 2-hop + sitasi graph.
    ("a", "bridge", "PT Sumber Rasa memasok bahan untuk menu apa saja?",
     ["pemasok.md"], True),
    ("a", "bridge", "Menu apa saja yang memakai Gula Aren?", ["pemasok.md"],
     True),
    ("a", "bridge", "Siapa pemasok bahan Menu Es Kopi Susu?", ["pemasok.md"],
     True),
    ("a", "bridge", "Teh Melati dipasok siapa dan dipakai di mana?",
     ["pemasok.md"], True),
    ("a", "bridge", "Di kota mana pemasok Kopi Arabika berada?",
     ["pemasok.md"], True),
    ("a", "bridge", "Dokumen apa yang diterbitkan atasan Budi Hartono?",
     ["tim.md"], True),
    ("a", "bridge", "Siapa kepala divisi tempat Laptop ThinkPad dipakai?",
     ["tim.md"], True),
    ("a", "bridge", "Siti Rahayu dan Budi Hartono bekerja sebagai apa?",
     ["tim.md"], True),
    # onehop (6).
    ("a", "onehop", "Siapa memasok Kopi Arabika?", ["pemasok.md"], False),
    ("a", "onehop", "PT Tiga Rasa memasok apa?", ["pemasok.md"], False),
    ("a", "onehop", "Budi Hartono bekerja sebagai apa?", ["tim.md"], False),
    ("a", "onehop", "PT Maju Jaya memiliki divisi apa?", ["tim.md"], False),
    ("a", "onehop", "PT Sumber Rasa berlokasi di mana?", ["pemasok.md"],
     False),
    ("a", "onehop", "Laptop ThinkPad dipakai di mana?", ["tim.md"], False),
    # alias (4).
    ("a", "alias", "Komputer jinjing dipakai di mana?", ["tim.md"], True),
    ("a", "alias", "Siapa memasok bahan ess?", ["pemasok.md"], True),
    ("a", "alias", "Jaminan komputer jinjing berlaku berapa lama?",
     ["kebijakan.md"], False),
    ("a", "alias", "Menu ess berisi apa?", ["pemasok.md"], False),
    # compare (3).
    ("a", "compare", "Harga kopi vs teh, mana lebih mahal?", ["harga.md"],
     False),
    ("a", "compare", "PT Sumber Rasa vs PT Tiga Rasa memasok apa saja?",
     ["pemasok.md"], False),
    ("a", "compare", "Siapa atasan Siti Rahayu dan Budi Hartono?",
     ["tim.md"], False),
    # neggraph (2): sumber benar + TANPA relasi salah.
    ("a", "neggraph", "Apakah PT Sumber Rasa memasok Teh Melati?",
     ["pemasok.md"], False),
    ("a", "neggraph", "Apakah Teh Melati dipakai di Menu Es Kopi Susu?",
     ["pemasok.md"], False),
    # vguard (2): canary gold30.
    ("a", "vguard", "Berapa lama masa retur barang?", ["kebijakan.md"],
     False),
    ("a", "vguard", "Berapa harga Teh Melati 100 gram?", ["harga.md"],
     False),
    # cross (2) + noanswer (1).
    ("b", "cross", "Siapa memasok Kopi Arabika?", None, False),
    ("a", "cross", "Kapan ada diskon member?", None, False),
    ("a", "noanswer", "Jadwal kereta ke Surabaya?", None, False),
]

ALIASES = {"komputer jinjing": "laptop thinkpad",
           "ess": "menu es kopi susu"}

# Sel ablation: (nama, graph_on, cap_agree, cap_default, max_hops, gfloor)
CELLS = [
    ("B0", False, 2, 6, 2, 0.05),
    ("FULL", True, 2, 6, 2, 0.05),
    ("CAP2", True, 2, 2, 2, 0.05),
    ("HOPS1", True, 2, 6, 1, 0.05),
    ("FL0", True, 2, 6, 2, 0.0),
    ("FL10", True, 2, 6, 2, 0.10),
]

FALSE_PAIRS = [
    ("pt sumber rasa", "teh melati", "memasok"),
    ("teh melati", "menu es kopi susu", None),
]


def _qdrant_client_or_none():
    from qdrant_client import AsyncQdrantClient

    url = (os.environ.get("TEST_QDRANT_URL")
           or os.environ.get("QDRANT_URL", "http://localhost:6333")).strip()
    key = (os.environ.get("TEST_QDRANT_API_KEY")
           or os.environ.get("QDRANT_API_KEY", "")).strip()
    if not key:
        return None
    return AsyncQdrantClient(url=url, api_key=key, timeout=10)


def _judge(stratum, res, expected, need_graph):
    chunks = res["chunks"]
    got = [c["source"] for c in chunks]
    graph_used = any((c.get("kind") or "") == "graph" for c in chunks)
    if expected is None:
        return res["status"] == "no_evidence", graph_used, ""
    n = sum(1 for s in got if s in expected)
    hit = n >= 1 and res["status"] == "ok"
    if need_graph and not graph_used:
        return False, graph_used, "tanpa sitasi graph"
    if stratum == "neggraph":
        for c in chunks:
            if (c.get("kind") or "") != "graph":
                continue
            text = (c.get("content") or "").lower()
            for s, o, rel in FALSE_PAIRS:
                if s in text and o in text and (
                        rel is None or rel in text):
                    return False, graph_used, f"relasi salah: {text[:60]}"
    return hit, graph_used, ""


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["pgvector", "qdrant"])
async def test_graph_gold_runner(backend, db_url, embed_url, tag):
    import shutil

    from app.core.config import Settings
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.extract.gliner import GlierExtractor
    from app.infra.rag.extract.l1_rules import L1RulesExtractor
    from app.infra.rag.extract.layered import LayeredExtractor
    from app.infra.rag.infinity import InfinityEmbeddings, InfinityReranker
    from app.infra.rag.pg_graph import PgGraphStore
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.infra.rag_migrate import apply_rag_migration
    from app.rag.grounding import check_graph_provenance
    from app.rag.pipeline import HybridRetriever
    from app.workers.ingest import WorkerCtx, build_parsers, process_job
    from conftest import purge_stale_test_jobs

    cache = os.environ.get("HF_HUB_CACHE", ".hf-cache")
    if not os.path.isdir(os.path.join(
            cache, "models--fastino--gliner2.5-multi-v1")):
        pytest.skip("model GLiNER2.5 belum diunduh")
    blob_root = Path(".test-blobs").resolve()
    settings = Settings(
        _env_file=None, DATABASE_URL=db_url, DB_POOL_MIN=1, DB_POOL_MAX=5,
        RAG_STORAGE_DIR=str(blob_root), RAG_BACKEND=backend,
        GRAPH_ENABLED=True,
        GRAPH_EXTRACTOR_MODEL="fastino/gliner2.5-multi-v1")
    pool = create_pool(settings)
    await open_pool(pool)
    await apply_rag_migration(pool)
    await purge_stale_test_jobs(pool)
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    rerank = InfinityReranker(embed_url, "BAAI/bge-reranker-v2-m3",
                              timeout_ms=120_000)
    tok = BgeTokenizer(cache_dir=".hf-cache")
    tenants = {suf: f"it-gg-{suf}-{tag}" for suf in ("a", "b")}
    qdrant_shared = None
    if backend == "qdrant":
        qdrant_shared = _qdrant_client_or_none()
        if qdrant_shared is None:
            pytest.skip("QDRANT_API_KEY kosong")
        try:
            await qdrant_shared.get_collections()
        except Exception as exc:
            await qdrant_shared.close()
            pytest.skip(f"Qdrant tak terjangkau ({type(exc).__name__})")
    extractor = LayeredExtractor(L1RulesExtractor(),
                                 GlierExtractor(cache_dir=cache), None)
    try:
        parsers, ocr = build_parsers(settings)
        for doc_id, (dsuf, fname, blob) in DOCS.items():
            t = tenants[dsuf]
            rel = f"{t}/doc-{doc_id}.bin"
            target = blob_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            doc = await repo.create_document(
                pool, tenant=t, source_type="md", source_uri=rel,
                title=fname, lang="id", size_bytes=len(blob),
                sha256=hashlib.sha256(
                    blob + t.encode() + doc_id.encode()).hexdigest(),
                parser="auto", chunk_preset="markdown",
                created_by="goldgraph")
            job = await repo.create_job(pool, tenant=t,
                                        document_id=str(doc["id"]))
            claimed = await repo.claim_job(pool, worker_id="goldgraph")
            assert claimed is not None and claimed["id"] == job["id"]
            ctx = WorkerCtx(
                settings=settings, pool=pool, worker_id="goldgraph",
                embed=embed, tok=tok, qdrant=qdrant_shared, cache=None,
                parsers=parsers, ocr=ocr, extractor=extractor)
            got = await process_job(ctx, claimed)
            assert got == "done", (doc_id, got)
        await PgGraphStore(pool, tenant=tenants["a"]).upsert_aliases(ALIASES)

        all_rows: dict[str, list] = {}
        for cell, gon, ca, cd, hops, gf in CELLS:
            rows = []
            for suf, stratum, q, expected, need_graph in GOLD:
                t = tenants[suf]
                client = None
                if backend == "qdrant":
                    from qdrant_client import AsyncQdrantClient

                    from app.infra.rag.qdrant import (QdrantStore,
                                                     collection_name)

                    url = (os.environ.get("TEST_QDRANT_URL")
                           or os.environ.get("QDRANT_URL",
                                             "http://localhost:6333")).strip()
                    key = (os.environ.get("TEST_QDRANT_API_KEY")
                           or os.environ.get("QDRANT_API_KEY", "")).strip()
                    client = AsyncQdrantClient(url=url, api_key=key,
                                               timeout=10)
                    store = QdrantStore(
                        client, collection_name(1024, "BAAI/bge-m3"),
                        tenant=t)
                else:
                    from app.infra.rag.pgvector import PgVectorStore

                    store = PgVectorStore(pool, tenant=t)
                try:
                    graph = (PgGraphStore(pool, tenant=t) if gon else None)
                    r = HybridRetriever(
                        embed=embed, rerank=rerank, store=store, tok=tok,
                        graph=graph, graph_cap_agree=ca,
                        graph_cap_default=cd, graph_max_hops=hops,
                        graph_score_floor=gf)
                    res = await r.search(q, k=5)
                finally:
                    if client is not None:
                        await client.close()
                hit, gused, note = _judge(stratum, res, expected,
                                          need_graph)
                gchunks = [c for c in res["chunks"]
                           if (c.get("kind") or "") == "graph"]
                g1 = check_graph_provenance(gchunks)
                rows.append({
                    "q": q, "stratum": stratum, "hit": hit,
                    "graph_used": gused, "g1_ok": not g1,
                    "status": res["status"],
                    "got": [c["source"] for c in res["chunks"]],
                    "top": res["trace"]["top_score"],
                    "ms": res["trace"]["timings_ms"]["total"],
                    "degraded": res["degraded"], "note": note})
            all_rows[cell] = rows
    finally:
        await embed.aclose()
        await rerank.aclose()
        if qdrant_shared is not None:
            await qdrant_shared.close()
        for t in tenants.values():
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM documents WHERE tenant=%s",
                                   (t,))
                await conn.execute(
                    "DELETE FROM kg_aliases WHERE tenant=%s", (t,))
            shutil.rmtree(blob_root / t, ignore_errors=True)
        await close_pool(pool)

    ARTIFACT.mkdir(parents=True, exist_ok=True)
    print(f"\n== gold-graph [{backend}] ==")
    summary = {}
    for cell, rows in all_rows.items():
        (ARTIFACT / f"gold_graph_{backend}_{cell}.json").write_text(
            json.dumps({"backend": backend, "cell": cell, "rows": rows},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        by_s: dict[str, list[bool]] = {}
        for row in rows:
            by_s.setdefault(row["stratum"], []).append(row["hit"])
        total = sum(sum(v) for v in by_s.values())
        ms = sorted(r["ms"] for r in rows)
        p50, p95 = ms[len(ms) // 2], ms[int(len(ms) * 0.95)]
        gused = sum(1 for r in rows if r["graph_used"])
        g1bad = sum(1 for r in rows if not r["g1_ok"])
        summary[cell] = {"total": total, "n": len(rows), "p50": p50,
                         "p95": p95, "gused": gused, "g1bad": g1bad,
                         "by_stratum": {k: sum(v) for k, v in by_s.items()}}
        print(f"  [{cell}] total={total}/{len(rows)} p50={p50:.0f} "
              f"p95={p95:.0f} gused={gused} g1bad={g1bad}")
        for s, hs in sorted(by_s.items()):
            print(f"    {s:9s} {sum(hs):2d}/{len(hs)}")
        for row in rows:
            if not row["hit"]:
                print(f"    MISS [{row['stratum']}] {row['q']} "
                      f"got={row['got']} {row['note']}")
    (ARTIFACT / f"gold_graph_{backend}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    # Gate keras F5: canary vguard + FPR=0 (T3). T1/T2/T4/T5/T7 → kalibrasi.
    for cell, rows in all_rows.items():
        vg = [r["hit"] for r in rows if r["stratum"] == "vguard"]
        assert vg == [True, True], f"{cell}: regresi vguard {vg}"
        bad = [r for r in rows
               if r["stratum"] in ("neggraph", "cross", "noanswer")
               and not r["hit"]]
        assert not bad, f"{cell}: FPR! {[(r['q'], r['note']) for r in bad]}"
    assert all(len(r) == 28 for r in all_rows.values())
