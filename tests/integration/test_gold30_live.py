"""Runner gold-30 v0.7.1 F2: UKUR kualitas retrieval, bukan gate.

Strata (30 QA): faktoid 5, parafrase 5, morfologi 4, angka-format 2,
tabel 3, negasi 2, typo 2, multi-bagian 1, cross-workspace 3, unanswerable 3.
Berjalan di pgvector + qdrant (bila terjangkau).

Satu-satunya assertion kualitas = canary faktoid 5/5 (sudah hijau di v0.7;
bila gagal = regresi nyata). Sisanya DICATAT: tabel per-stratum + JSON
artefak untuk keputusan gerbang CRAG (F4).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ARTIFACT = (Path(__file__).resolve().parents[2] / ".." / "Test" / "spike_v07")

DOCS: dict[str, tuple[str, str, bytes]] = {
    # doc_id: (workspace_suffix, filename, bytes)
    "kebijakan": ("a", "kebijakan.md",
                  "## Kebijakan Toko Maju Jaya\n\nBarang bisa diretur maksimal "
                  "14 hari dengan nota pembelian.\n\nGaransi laptop 24 bulan "
                  "sejak tanggal beli.\n\nGratis ongkos kirim untuk belanja di "
                  "atas Rp 500.000.\n\nJam operasional Senin sampai Sabtu "
                  "pukul 09.00-21.00.\n".encode()),
    "harga": ("a", "harga.md",
              "## Daftar Harga\n\n| Produk | Kemasan | Harga |\n"
              "|---|---|---|\n| Kopi Arabika | 200 gram | 50000 |\n"
              "| Teh Melati | 100 gram | 25000 |\n"
              "| Gula Aren | 1 kg | 35000 |\n".encode()),
    "kirim": ("a", "pengiriman.md",
              "## Pengiriman\n\nLayanan SAME DAY khusus Jabodetabek, batas "
              "pemesanan pukul 15.00.\n\nReguler tiba 2-3 hari kerja.\n\n"
              "Asuransi wajib untuk belanja di atas 2 juta rupiah, gratis "
              "untuk member gold.\n".encode()),
    "garansi": ("a", "garansi.md",
                "## Klaim Garansi\n\nKirim email ke klaim@majujaya.id maksimal "
                "3x24 jam setelah barang diterima.\n\nBawa nota pembelian dan "
                "kartu garansi saat klaim.\n\nPerbaikan servis selesai dalam "
                "7 hari kerja.\n".encode()),
    "promo": ("b", "promo.md",
              "## Promo Member\n\nDiskon member 10 persen setiap hari Jumat.\n\n"
              "Satu poin untuk tiap belanja sepuluh ribu rupiah.\n\n"
              "Lima ratus poin dapat ditukar voucher lima puluh ribu.\n"
              .encode()),
    "returb": ("b", "sop-retur.md",
               "## Retur Elektronik\n\nBarang elektronik hanya bisa diretur "
               "7 hari dan segel tidak rusak.\n\nDana kembali dalam 5 hari "
               "kerja ke rekening pelanggan.\n".encode()),
}

# (workspace_suffix, stratum, question, expected_sources|None, min_chunks)
GOLD: list[tuple[str, str, str, list[str] | None, int]] = [
    ("a", "faktoid", "Berapa lama masa retur barang?", ["kebijakan.md"], 1),
    ("a", "faktoid", "Garansi laptop berapa bulan?", ["kebijakan.md"], 1),
    ("a", "faktoid", "Minimal belanja berapa agar gratis ongkir?",
     ["kebijakan.md"], 1),
    ("a", "faktoid", "Berapa harga Teh Melati 100 gram?", ["harga.md"], 1),
    ("b", "faktoid", "Berapa persen diskon member hari Jumat?",
     ["promo.md"], 1),
    ("a", "parafrase", "Sampai kapan saya bisa mengembalikan pembelian?",
     ["kebijakan.md"], 1),
    ("a", "parafrase", "Jaminan komputer jinjing berlaku berapa lama?",
     ["kebijakan.md"], 1),
    ("a", "parafrase", "Syarat bebas biaya kirim?", ["kebijakan.md"], 1),
    ("b", "parafrase", "Kapan potongan harga anggota berlaku?", ["promo.md"],
     1),
    ("a", "parafrase", "Harganya kopi arabika yang 200 gram?", ["harga.md"],
     1),
    ("a", "morfologi", "Prosedur pengembalian barang seperti apa?",
     ["kebijakan.md"], 1),
    ("a", "morfologi", "Laptop yang digaransikan syaratnya apa?",
     ["kebijakan.md", "garansi.md"], 1),
    ("a", "morfologi", "Pengiriman sameday batas pemesanannya jam berapa?",
     ["pengiriman.md"], 1),
    ("b", "morfologi", "Penukaran poin member bagaimana?", ["promo.md"], 1),
    ("a", "angka", "Gula aren sekilo berapa?", ["harga.md"], 1),
    ("a", "angka", "Ongkir gratis untuk belanja lima ratus ribu?",
     ["kebijakan.md"], 1),
    ("a", "tabel", "Produk apa yang harganya 35 ribu?", ["harga.md"], 1),
    ("a", "tabel", "Harga kopi vs teh, mana lebih mahal?", ["harga.md"], 1),
    ("b", "tabel", "500 poin ditukar dapat apa?", ["promo.md"], 1),
    ("a", "negasi", "Apakah semua produk bisa diretur?", ["kebijakan.md"], 1),
    ("a", "negasi", "Asuransi gratis untuk semua pembeli?",
     ["pengiriman.md"], 1),
    ("a", "typo", "Berapa lama garansi laptpo?", ["kebijakan.md"], 1),
    ("b", "typo", "Diskon meber hari apa?", ["promo.md"], 1),
    ("a", "multi", "Syarat klaim garansi dan berapa lama servisnya?",
     ["garansi.md"], 1),
    ("a", "cross", "Kapan ada diskon member?", None, 0),
    ("b", "cross", "Berapa harga Kopi Arabika?", None, 0),
    ("a", "cross", "Berapa lama retur elektronik?", None, 0),
    ("a", "noanswer", "Jadwal kereta ke Surabaya?", None, 0),
    ("a", "noanswer", "Berapa gaji karyawan toko?", None, 0),
    ("b", "noanswer", "Di mana lokasi pabrik kopi?", None, 0),
]


def _qdrant_client_or_skip():
    import os

    from qdrant_client import AsyncQdrantClient

    url = (os.environ.get("TEST_QDRANT_URL")
           or os.environ.get("QDRANT_URL", "http://localhost:6333")).strip()
    key = (os.environ.get("TEST_QDRANT_API_KEY")
           or os.environ.get("QDRANT_API_KEY", "")).strip()
    if not key:
        pytest.skip("QDRANT_API_KEY kosong â€” samakan dengan Infra/.env")
    return AsyncQdrantClient(url=url, api_key=key, timeout=10)




@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["pgvector", "qdrant"])
async def test_gold30_runner(backend, db_url, embed_url, tag, tmp_path):
    """Ukur 30 QA (canary faktoid = satu-satunya gate)."""
    import os

    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.infinity import InfinityEmbeddings, InfinityReranker
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.infra.rag_migrate import apply_rag_migration
    from app.rag.pipeline import HybridRetriever

    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_STORAGE_DIR=str(tmp_path),
                        RAG_BACKEND=backend)
    pool = create_pool(settings)
    await open_pool(pool)
    await apply_rag_migration(pool)
    embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                               timeout_ms=120_000)
    rerank = InfinityReranker(embed_url, "BAAI/bge-reranker-v2-m3",
                              timeout_ms=120_000)
    tok = BgeTokenizer(cache_dir=".hf-cache")
    workspaces = {suf: f"it-g30-{suf}-{tag}" for suf in ("a", "b")}
    # Seed memakai workspace per-doc (isolasi antar-doc dalam 1 backend run).
    doc_workspaces: dict[str, str] = {}
    try:
        for suf in ("a", "b"):
            t = workspaces[suf]
            # Tandai workspace per-doc agar cross-workspace terukur murni.
            for doc_id, (dsuf, _fn, _b) in DOCS.items():
                if dsuf == suf:
                    doc_workspaces[doc_id] = t
        for suf, t in workspaces.items():
            # Seed dengan namespace workspace uji (purge aman: prefix it-).
            import app.infra.ingest_repo as repo

            from app.workers.ingest import (WorkerCtx, build_parsers,
                                            process_job)
            from conftest import purge_stale_test_jobs

            await purge_stale_test_jobs(pool)
            parsers, ocr = build_parsers(settings)
            qdrant = None
            if backend == "qdrant":
                client = _qdrant_client_or_skip()
                try:
                    await client.get_collections()
                except Exception as exc:
                    await client.close()
                    pytest.skip(
                        f"Qdrant tak terjangkau ({type(exc).__name__})")
                from app.infra.rag.qdrant import (QdrantStore,
                                                  collection_name)

                probe = QdrantStore(
                    client, collection_name(1024, "BAAI/bge-m3"),
                    workspace="__probe__")
                await probe.ensure_collection()
                qdrant = client
            try:
                for doc_id, (dsuf, fname, blob) in DOCS.items():
                    if dsuf != suf:
                        continue
                    rel = f"{t}/doc-{doc_id}.bin"
                    target = tmp_path / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blob)
                    doc = await repo.create_document(
                        pool, workspace=t, source_type="md",
                        source_uri=rel, title=fname, lang="id",
                        size_bytes=len(blob),
                        sha256=hashlib.sha256(
                            blob + t.encode() + doc_id.encode()).hexdigest(),
                        parser="auto", chunk_preset="markdown",
                        created_by="gold30")
                    job = await repo.create_job(
                        pool, workspace=t, document_id=str(doc["id"]))
                    claimed = await repo.claim_job(pool,
                                                   worker_id="gold30")
                    assert claimed is not None
                    assert claimed["id"] == job["id"]
                    ctx = WorkerCtx(
                        settings=settings, pool=pool, worker_id="gold30",
                        embed=embed, tok=tok, qdrant=qdrant, cache=None,
                        parsers=parsers, ocr=ocr)
                    got = await process_job(ctx, claimed)
                    assert got == "done", (doc_id, got)
            finally:
                if qdrant is not None:
                    await qdrant.close()

        rows = []
        for suf, stratum, q, expected, min_n in GOLD:
            t = workspaces[suf]
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
                    client, collection_name(1024, "BAAI/bge-m3"), workspace=t)
            else:
                from app.infra.rag.pgvector import PgVectorStore

                client = None
                store = PgVectorStore(pool, workspace=t)
            try:
                r = HybridRetriever(embed=embed, rerank=rerank, store=store,
                                    tok=tok)
                res = await r.search(q, k=5)
            finally:
                if client is not None:
                    await client.close()
            got_sources = [c["source"] for c in res["chunks"]]
            if expected is None:
                hit = res["status"] == "no_evidence"
            else:
                n = sum(1 for s in got_sources if s in expected)
                hit = n >= min_n and res["status"] == "ok"
            rows.append({"q": q, "stratum": stratum, "expected": expected,
                         "got": got_sources, "status": res["status"],
                         "hit": hit, "top": res["trace"]["top_score"],
                         "ms": res["trace"]["timings_ms"]["total"]})
    finally:
        await embed.aclose()
        await rerank.aclose()
        for t in workspaces.values():
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM documents WHERE workspace=%s",
                                   (t,))
        await close_pool(pool)

    # Artefak JSON untuk F4 (per backend).
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT / f"gold30_{backend}.json").write_text(
        json.dumps({"backend": backend, "rows": rows}, ensure_ascii=False,
                   indent=1), encoding="utf-8")
    # Ringkasan per stratum (terlihat di -s / CI log).
    by_stratum: dict[str, list[bool]] = {}
    for row in rows:
        by_stratum.setdefault(row["stratum"], []).append(row["hit"])
    print(f"\n== gold30 [{backend}] ==")
    total_hit = 0
    for s, hs in sorted(by_stratum.items()):
        print(f"  {s:10s} {sum(hs):2d}/{len(hs)}")
        total_hit += sum(hs)
    print(f"  {'TOTAL':10s} {total_hit:2d}/{len(rows)} "
          f"(fail={len(rows) - total_hit})")
    for row in rows:
        if not row["hit"]:
            print(f"  MISS [{row['stratum']}] {row['q']} -> got={row['got']} "
                  f"top={row['top']} status={row['status']}")
    # Canary: faktoid wajib 5/5 (gate regresi); sisanya diukur.
    assert by_stratum["faktoid"] == [True] * 5, "regresi faktoid!"
    assert len(rows) == 30
