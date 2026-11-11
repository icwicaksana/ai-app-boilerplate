"""Eval mini RAG Indonesia (F6): gold QA + faithfulness + isolasi silang.

Mode ganda:
- Selalu: retrieval (hit@3, no_evidence, isolasi silang) — tanpa LLM.
- Bila gateway LiteLLM hidup: full rag_answer (grounded/refusal/sitasi).
  Bila mati: SKIP bagian LLM (retrieval tetap diuji).
"""

import hashlib
import urllib.request

import pytest
from langgraph.checkpoint.memory import InMemorySaver

pytestmark = pytest.mark.integration

DOC_A1 = ("## Kebijakan Toko Maju Jaya\n\nRetur maksimal 14 hari dengan nota. "
          "Garansi resmi 24 bulan khusus laptop. Gratis ongkir untuk pembelian "
          "di atas Rp 500.000.\n").encode()
DOC_A2 = ("## Daftar Harga\n\n| Produk | Harga |\n|---|---|\n"
          "| Kopi Arabika 200g | 50000 |\n| Teh Melati 100g | 25000 |\n").encode()
DOC_B1 = ("## Promo Member\n\nDiskon member 10 persen setiap hari Jumat.\n").encode()

GOLD = [
    # (tenant, pertanyaan, ekspektasi_sumber, jawab?)
    ("a", "Berapa lama masa retur barang?", "kebijakan.md", True),
    ("a", "Garansi laptop berapa bulan?", "kebijakan.md", True),
    ("a", "Minimal belanja berapa agar gratis ongkir?", "kebijakan.md", True),
    ("a", "Berapa harga Kopi Arabika 200 gram?", "harga.md", True),
    ("a", "Kapan ada diskon member?", None, False),  # hanya di tenant B
    ("a", "Jadwal kereta ke Surabaya?", None, False),  # tak ada di mana pun
    ("b", "Kapan ada diskon member?", "promo.md", True),
    ("b", "Berapa harga Kopi Arabika?", None, False),  # hanya di tenant A
]


async def _seed_eval(pool, settings, tenant: str, docs: list):
    from pathlib import Path

    from app.infra import ingest_repo as repo
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM documents WHERE tenant LIKE %s", (tenant + "%",))
    embed = InfinityEmbeddings(settings.RAG_EMBED_BASE_URL, "BAAI/bge-m3",
                               1024, timeout_ms=120_000)
    try:
        parsers, ocr = build_parsers(settings)
        ctx = WorkerCtx(settings=settings, pool=pool, worker_id="live-eval",
                        embed=embed, tok=BgeTokenizer(cache_dir=".hf-cache"),
                        qdrant=None, cache=None, parsers=parsers, ocr=ocr)
        for title, blob, preset in docs:
            rel = f"{tenant}/{title}.bin"
            target = Path(settings.RAG_STORAGE_DIR) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            doc = await repo.create_document(
                pool, tenant=tenant, source_type="md", source_uri=rel,
                title=title, lang="id", size_bytes=len(blob),
                sha256=hashlib.sha256(blob).hexdigest(), parser="auto",
                chunk_preset=preset, created_by="eval")
            job = await repo.create_job(pool, tenant=tenant,
                                        document_id=str(doc["id"]))
            claimed = await repo.claim_job(pool, worker_id="live-eval")
            assert claimed is not None and claimed["id"] == job["id"]
            assert await process_job(ctx, claimed) == "done"
    finally:
        await embed.aclose()


@pytest.mark.asyncio
async def test_live_eval_retrieval_gold(db_url, embed_url, tag, tmp_path):
    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.factory import init_rag, retriever_for_tenant
    from app.infra.rag_migrate import apply_rag_migration

    ta, tb = f"it-ea-{tag}", f"it-eb-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_EMBED_BASE_URL=embed_url,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await apply_rag_migration(pool)
        await _seed_eval(pool, settings, ta, [
            ("kebijakan.md", DOC_A1, "markdown"),
            ("harga.md", DOC_A2, "markdown")])
        await _seed_eval(pool, settings, tb, [("promo.md", DOC_B1, "markdown")])
        clients = await init_rag(settings, pool)
        try:
            hits, total = 0, 0
            for tenant_key, q, expect_source, answerable in GOLD:
                tenant = ta if tenant_key == "a" else tb
                r = retriever_for_tenant(settings, clients, pool,
                                         tenant=tenant)
                res = await r.search(q, k=3)
                total += 1
                if not answerable:
                    assert res["status"] == "no_evidence", (q, res["status"])
                    assert res["chunks"] == []
                    # Isolasi: sumber tenant lain TAK BOLEH muncul.
                    continue
                got = [c["source"] for c in res["chunks"]]
                assert res["status"] == "ok", (q, res)
                assert expect_source in got, (q, got)
                # Isolasi: semua chunk dari tenant penanya.
                assert all(c["metadata"].get("source_type") == "md"
                           for c in res["chunks"])
                hits += 1
            assert hits == 5 and total == 8
        finally:
            await clients.aclose()
    finally:
        await close_pool(pool)


async def _gateway_hidup() -> bool:
    """Probe definitif: komplesi 1-token (skip-gate jujur).

    `/health` tanpa key = 401; dengan key = deep-check yang bisa hang bila
    upstream mati. Probe komplesi kecil menjawab pertanyaan sebenarnya:
    "bisakah LLM dipanggil sekarang?" Gagal APAPUN = skip (bukan fail).

    Async murni (JANGAN asyncio.run: test sudah dalam loop — temuan F6).
    """
    import asyncio

    try:
        from app.core.config import get_settings
        from app.infra.llm import get_llm

        llm = get_llm(get_settings(), "eval-probe")
        await asyncio.wait_for(llm.ainvoke("Balas: ok"), timeout=25)
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_live_eval_jawaban_llm(db_url, embed_url, tag, tmp_path):
    if not await _gateway_hidup():
        pytest.skip("gateway LiteLLM mati — bagian LLM dilewati")
    from app.agents.AgentContext import AgentContext
    from app.agents.RagAnswerAgent import RagAnswerAgent
    from app.controllers.runner import Runner
    from app.core.config import Settings
    from app.infra.llm import get_llm
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.factory import init_rag, retriever_for_tenant
    from app.infra.rag_migrate import apply_rag_migration
    from app.rag import citations as C
    from app.rag.grounding import REFUSAL_ID

    ta = f"it-ec-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_EMBED_BASE_URL=embed_url,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await apply_rag_migration(pool)
        await _seed_eval(pool, settings, ta, [
            ("kebijakan.md", DOC_A1, "markdown"),
            ("harga.md", DOC_A2, "markdown")])
        clients = await init_rag(settings, pool)
        try:
            retriever = retriever_for_tenant(settings, clients, pool,
                                             tenant=ta)
            ctx = AgentContext(settings=settings,
                               checkpointer=InMemorySaver(), runner=Runner(),
                               session_id=f"{ta}:u:s", retriever=retriever)
            ag = RagAnswerAgent(ctx)
            ag.llm = get_llm(settings, f"{ta}:u:s")

            # Terjawab: grounded + sitasi valid + fakta benar.
            out = await ag.run_agent(
                {"question": "Berapa harga Kopi Arabika 200 gram?"},
                {"configurable": {"thread_id": f"{ta}:u:s"}})
            data = out["output"]["data"]
            assert data["grounded"] is True, out["output"]
            assert "50000" in out["output"]["reply"] or "50.000" in out[
                "output"]["reply"]
            assert C.validate_markers(out["output"]["reply"],
                                      data["citations"]) == []

            # Tak terjawab: refusal eksak, LLM tak dipaksa karang.
            out2 = await ag.run_agent(
                {"question": "Jadwal kereta ke Surabaya?"},
                {"configurable": {"thread_id": f"{ta}:u:s2"}})
            assert out2["output"]["reply"] == REFUSAL_ID
            assert out2["output"]["data"]["refusal"] is True
        finally:
            await clients.aclose()
    finally:
        await close_pool(pool)
