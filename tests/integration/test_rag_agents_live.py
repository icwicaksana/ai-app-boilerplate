"""Live agen RAG: retrieval NYATA (Infinity + PG) + LLM fake.

Full `run_agent` rag_answer: retrieve nyata → generate (fake terskrip) →
finalize gate nyata. Butuh app-db + Infinity.
"""

import hashlib

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

pytestmark = pytest.mark.integration

MD = ("## Kebijakan Garansi\n\nGaransi resmi 24 bulan untuk semua laptop. "
      "Klaim dengan nota pembelian.\n\n| Produk | Garansi |\n|---|---|\n"
      "| Laptop X1 | 24 bulan |\n").encode()


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    async def ainvoke(self, prompt):
        assert "DATA DOKUMEN" in prompt  # konteks ter-quoting masuk LLM
        return AIMessage(content=self.text)


async def _seed_ready(pool, settings, workspace: str):
    """Seed MD + proses inline sampai ready. Return document_id."""
    from app.infra import ingest_repo as repo
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.workers.ingest import WorkerCtx, build_parsers, process_job
    from conftest import purge_stale_test_jobs

    await purge_stale_test_jobs(pool)  # queue global: buang job basi workspace test

    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM documents WHERE workspace LIKE %s", (workspace + "%",))
    from pathlib import Path

    rel = f"{workspace}/garansi.bin"
    target = Path(settings.RAG_STORAGE_DIR) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(MD)
    doc = await repo.create_document(
        pool, workspace=workspace, source_type="md", source_uri=rel,
        title="garansi.md", lang="id", size_bytes=len(MD),
        sha256=hashlib.sha256(MD).hexdigest(), parser="auto",
        chunk_preset="markdown", created_by="live")
    job = await repo.create_job(pool, workspace=workspace,
                                document_id=str(doc["id"]))
    claimed = await repo.claim_job(pool, worker_id="live-ra")
    assert claimed is not None and claimed["id"] == job["id"]
    embed = InfinityEmbeddings(settings.RAG_EMBED_BASE_URL, "BAAI/bge-m3",
                               1024, timeout_ms=120_000)
    try:
        parsers, ocr = build_parsers(settings)
        ctx = WorkerCtx(settings=settings, pool=pool, worker_id="live-ra",
                        embed=embed, tok=BgeTokenizer(cache_dir=".hf-cache"),
                        qdrant=None, cache=None, parsers=parsers, ocr=ocr)
        assert await process_job(ctx, claimed) == "done"
    finally:
        await embed.aclose()
    return str(doc["id"])


@pytest.mark.asyncio
async def test_live_tool_dan_retrieve_nyata(db_url, embed_url, tag, tmp_path):
    from app.agents.tools.RetrieverTool import make_search_knowledge_tool
    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.factory import init_rag, retriever_for_workspace
    from app.infra.rag_migrate import apply_rag_migration

    workspace = f"it-ra-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        RAG_EMBED_BASE_URL=embed_url,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await apply_rag_migration(pool)
        await _seed_ready(pool, settings, workspace)
        clients = await init_rag(settings, pool)
        retriever = retriever_for_workspace(settings, clients, pool,
                                         workspace=workspace)
        tool = make_search_knowledge_tool(retriever)
        out = await tool.ainvoke({"query": "berapa lama garansi laptop?"})
        assert "[^1]" in out and "24 bulan" in out
        assert "garansi.md" in out
        # Workspace KOSONG (tanpa dokumen) → deterministik no_evidence.
        # (Query tak-terkait di index berisi = 'ok' skor-rendah; itu ranah
        # kalibrasi floor F6, bukan assertion di sini.)
        empty_retriever = retriever_for_workspace(
            settings, clients, pool, workspace=f"it-ra-kosong-{tag}")
        idle = await make_search_knowledge_tool(
            empty_retriever).ainvoke({"query": "jadwal kereta ke Surabaya?"})
        assert "TIDAK ADA BUKTI" in idle
        await clients.aclose()
    finally:
        await close_pool(pool)


@pytest.mark.asyncio
async def test_live_answer_full_run_grounded(db_url, embed_url, tag, tmp_path):
    from app.agents.AgentContext import AgentContext
    from app.agents.RagAnswerAgent import RagAnswerAgent
    from app.controllers.runner import Runner
    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.factory import init_rag, retriever_for_workspace
    from app.infra.rag_migrate import apply_rag_migration

    workspace = f"it-rb-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        LITELLM_BASE_URL="http://localhost:4000",
                        LITELLM_API_KEY="x", LITELLM_MODEL="m",
                        RAG_EMBED_BASE_URL=embed_url,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await apply_rag_migration(pool)
        await _seed_ready(pool, settings, workspace)
        clients = await init_rag(settings, pool)
        retriever = retriever_for_workspace(settings, clients, pool,
                                         workspace=workspace)
        ctx = AgentContext(settings=settings,
                           checkpointer=InMemorySaver(), runner=Runner(),
                           session_id=f"{workspace}:u:s", retriever=retriever)
        ag = RagAnswerAgent(ctx)
        ag.llm = _FakeLLM("Garansi resmi 24 bulan untuk semua laptop.[^1]")
        out = await ag.run_agent(
            {"question": "berapa lama garansi laptop?"},
            {"configurable": {"thread_id": f"{workspace}:u:s"}})
        assert out["status"] == "completed"
        data = out["output"]["data"]
        assert data["grounded"] is True and data["refusal"] is False
        assert data["retrieval_status"] == "ok"
        assert len(data["citations"]) >= 1
        assert "Sumber:" in out["output"]["reply"]
        await clients.aclose()
    finally:
        await close_pool(pool)


@pytest.mark.asyncio
async def test_live_answer_no_evidence_tanpa_llm(db_url, embed_url, tag,
                                                 tmp_path):
    from app.agents.AgentContext import AgentContext
    from app.agents.RagAnswerAgent import RagAnswerAgent
    from app.controllers.runner import Runner
    from app.core.config import Settings
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.factory import init_rag, retriever_for_workspace
    from app.infra.rag_migrate import apply_rag_migration
    from app.rag.grounding import REFUSAL_ID

    workspace = f"it-rc-{tag}"
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        DB_POOL_MIN=1, DB_POOL_MAX=5,
                        LITELLM_BASE_URL="http://localhost:4000",
                        LITELLM_API_KEY="x", LITELLM_MODEL="m",
                        RAG_EMBED_BASE_URL=embed_url,
                        RAG_STORAGE_DIR=str(tmp_path))
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await apply_rag_migration(pool)
        # SENGAJA tanpa seed: workspace kosong → no_evidence deterministik.
        clients = await init_rag(settings, pool)
        retriever = retriever_for_workspace(settings, clients, pool,
                                         workspace=workspace)
        ctx = AgentContext(settings=settings,
                           checkpointer=InMemorySaver(), runner=Runner(),
                           session_id=f"{workspace}:u:s", retriever=retriever)
        ag = RagAnswerAgent(ctx)

        class _Boom:
            async def ainvoke(self, prompt):
                raise AssertionError("LLM tak boleh dipanggil!")

        ag.llm = _Boom()
        out = await ag.run_agent(
            {"question": "jadwal kereta ke Surabaya?"},
            {"configurable": {"thread_id": f"{workspace}:u:s"}})
        assert out["output"]["reply"] == REFUSAL_ID
        assert out["output"]["data"]["refusal"] is True
        assert out["output"]["data"]["retrieval_status"] == "no_evidence"
        await clients.aclose()
    finally:
        await close_pool(pool)
