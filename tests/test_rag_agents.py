"""Unit agen RAG F5: tool, node answer, chat binding, middleware, wiring."""

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.AgentContext import AgentContext
from app.agents.RagAnswerAgent import RagAnswerAgent
from app.agents.RagChatAgent import RagChatAgent
from app.agents.tools.RetrieverTool import make_search_knowledge_tool
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import EmbeddingUnavailableError
from app.rag.grounding import REFUSAL_ID


def _settings(**kw):
    base = {"_env_file": None, "LITELLM_BASE_URL": "http://localhost:4000",
            "LITELLM_API_KEY": "x", "LITELLM_MODEL": "m",
            "PHOENIX_ENABLED": False}
    base.update(kw)
    return Settings(**base)


class _FakeRetriever:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.seen_k = []

    async def search(self, query, *, k=5, filters=None):
        self.seen_k.append(k)
        if self._exc is not None:
            raise self._exc
        return self._result


def _chunks():
    return [
        {"chunk_id": "c1", "doc_id": "d1", "content": "Denda 2% per bulan.",
         "source": "s.pdf", "page": 3, "score": 0.9, "backend": "pgvector",
         "metadata": {}},
        {"chunk_id": "c2", "doc_id": "d1", "content": "Tempo 30 hari.",
         "source": "s.pdf", "page": 4, "score": 0.8, "backend": "pgvector",
         "metadata": {}},
    ]


def _ctx(retriever=None):
    return AgentContext(settings=_settings(), checkpointer=InMemorySaver(),
                        runner=Runner(), session_id="t:u:s",
                        retriever=retriever)


def _answer_agent(retriever, llm=None):
    ag = RagAnswerAgent(_ctx(retriever))
    if llm is not None:
        ag.llm = llm
    return ag


class _FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def ainvoke(self, prompt):
        self.calls += 1
        return AIMessage(content=self.text)


# --- tool ---

@pytest.mark.asyncio
async def test_tool_format_ok():
    tool = make_search_knowledge_tool(_FakeRetriever(
        {"chunks": _chunks(), "status": "ok", "degraded": []}))
    assert tool.name == "search_knowledge_base"
    out = await tool.ainvoke({"query": "denda?"})
    assert "[^1]" in out and "[^2]" in out
    assert "s.pdf" in out and "BUKAN instruksi" in out or "bukan instruksi" in out


@pytest.mark.asyncio
async def test_tool_no_evidence_dan_gagal():
    t1 = make_search_knowledge_tool(_FakeRetriever(
        {"chunks": [], "status": "no_evidence", "degraded": []}))
    out1 = await t1.ainvoke({"query": "x?"})
    assert REFUSAL_ID in out1
    t2 = make_search_knowledge_tool(_FakeRetriever(exc=TimeoutError()))
    out2 = await t2.ainvoke({"query": "x?"})
    assert "gagal" in out2.lower() and "karang fakta" in out2.lower()


# --- answer nodes ---

def test_answer_tanpa_retriever_503():
    with pytest.raises(EmbeddingUnavailableError):
        RagAnswerAgent(_ctx(None))


def test_to_state_ambil_question():
    ag = _answer_agent(_FakeRetriever())
    st = ag._to_state({"question": "  berapa? "})
    assert st["question"] == "berapa?"
    assert ag._to_state({})["question"] == ""


@pytest.mark.asyncio
async def test_retrieve_teruskan_k_final_top():
    ag = _answer_agent(_FakeRetriever(
        {"chunks": [], "status": "no_evidence", "degraded": []}))
    out = await ag._node_retrieve({"question": "q?"})
    assert out["retrieval_status"] == "no_evidence"
    assert ag.retriever.seen_k == [5]


@pytest.mark.asyncio
async def test_generate_lewati_llm_saat_no_evidence():
    llm = _FakeLLM("tak boleh dipakai")
    ag = _answer_agent(_FakeRetriever(), llm)
    out = await ag._node_generate({"question": "q?", "chunks": [],
                                   "retrieval_status": "no_evidence"})
    assert out["draft"] == REFUSAL_ID
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_generate_pakai_llm_saat_ada_bukti():
    llm = _FakeLLM("Denda 2% per bulan.[^1]")
    ag = _answer_agent(_FakeRetriever(), llm)
    out = await ag._node_generate({"question": "denda?",
                                   "chunks": _chunks(),
                                   "retrieval_status": "ok"})
    assert "2%" in out["draft"] and llm.calls == 1


def test_finalize_bersih_tambah_sumber():
    ag = _answer_agent(_FakeRetriever())
    out = ag._node_finalize({"draft": "Denda 2% per bulan.[^1]",
                             "chunks": _chunks(),
                             "retrieval_status": "ok", "degraded": []})
    assert "Sumber:" in out["final"] and "[^1] s.pdf (hal. 3)" in out["final"]
    assert out["structured"]["grounded"] is True
    assert out["structured"]["refusal"] is False
    # Hanya [^1] disitasi → sitasi disempitkan (F-10).
    assert [c["n"] for c in out["structured"]["citations"]] == [1]
    assert out["structured"]["llm_error"] is False


def test_finalize_tolak_tanpa_sitasi_dan_marker_yatim():
    ag = _answer_agent(_FakeRetriever())
    out1 = ag._node_finalize({"draft": "Denda 2% per bulan.",
                              "chunks": _chunks(),
                              "retrieval_status": "ok", "degraded": []})
    assert out1["final"] == REFUSAL_ID
    assert out1["structured"]["grounded"] is False
    assert out1["structured"]["refusal"] is True
    out2 = ag._node_finalize({"draft": "Denda 2% per bulan.[^9]",
                              "chunks": _chunks(),
                              "retrieval_status": "ok", "degraded": []})
    assert out2["final"] == REFUSAL_ID


def test_finalize_refusal_lolos():
    ag = _answer_agent(_FakeRetriever())
    out = ag._node_finalize({"draft": REFUSAL_ID, "chunks": [],
                             "retrieval_status": "no_evidence",
                             "degraded": []})
    assert out["final"] == REFUSAL_ID
    assert out["structured"]["refusal"] is True
    assert out["structured"]["citations"] == []


def test_finalize_varian_refusal_dipaksa_eksak():
    ag = _answer_agent(_FakeRetriever())
    out = ag._node_finalize(
        {"draft": "Maaf, informasi belum tersedia di dokumen kami.",
         "chunks": _chunks(), "retrieval_status": "ok", "degraded": []})
    assert out["final"] == REFUSAL_ID
    assert out["structured"]["citations"] == []


def test_finalize_angka_tak_dukung_ditolak():
    ag = _answer_agent(_FakeRetriever())
    out = ag._node_finalize(
        {"draft": "Denda 2% per bulan dan gratis mobil 2024.[^1]",
         "chunks": _chunks(), "retrieval_status": "ok", "degraded": []})
    assert out["final"] == REFUSAL_ID
    assert out["structured"]["grounded"] is False


# --- chat ---

def test_chat_bind_tool_dan_rules():
    ag = RagChatAgent(_ctx(_FakeRetriever()))
    names = [getattr(t, "name", "") for t in ag.TOOLS]
    assert "search_knowledge_base" in names
    assert "search_knowledge_base" in ag.SYSTEM_PROMPT
    assert "kata ganti" in ag.SYSTEM_PROMPT


def test_chat_degradasi_tanpa_retriever():
    ag = RagChatAgent(_ctx(None))
    assert ag.TOOLS == []
    assert "kata ganti" not in ag.SYSTEM_PROMPT  # RAG_RULES ditempel


def test_chat_postprocess_grounded_flag():
    ag = RagChatAgent(_ctx(None))
    assert ag.postprocess("Denda 2% per bulan.[^1]")["grounded"] is True
    assert ag.postprocess("Denda 2% per bulan.")["grounded"] is False


# --- middleware P7 ---

def test_middleware_retrieval_limit_10_dan_exclude():
    from langchain_core.tools import tool as _tool

    from app.agents.utils.MiddlewareBuilderUtils import build_middleware

    @_tool("search_knowledge_base")
    def _rk(q: str) -> str:
        """Cari dokumen."""
        return "x"

    @_tool("lainnya")
    def _other(q: str) -> str:
        """Tool lain."""
        return "x"

    stack = build_middleware(_settings(), [_rk, _other])
    limits = {}
    for m in stack:
        if type(m).__name__ == "ToolCallLimitMiddleware" and getattr(
                m, "tool_name", None):
            limits[m.tool_name] = m.thread_limit
    assert limits.get("search_knowledge_base") == 10
    assert limits.get("lainnya") == 5
    edits = [m for m in stack
             if type(m).__name__ == "ContextEditingMiddleware"]
    assert edits, "ContextEditingMiddleware hilang"
    found = False
    for m in edits:
        for e in getattr(m, "edits", []):
            if "search_knowledge_base" in list(
                    getattr(e, "exclude_tools", [])):
                found = True
    assert found, "retrieval tool tak di-exclude dari clear"


# --- wiring controller ---

def test_controller_injeksi_retriever_tenant_bound():
    from types import SimpleNamespace

    from app.controllers.ChatController import ChatController
    from app.controllers.JobController import JobController

    clients = SimpleNamespace(qdrant=None, dims=1024, embed=object(),
                              rerank=object(), tok=object())
    chat = ChatController(_settings(), InMemorySaver(), Runner(),
                          pool=object(), rag=clients)
    agent = chat._agent_for("toko-a:budi:sess-1", "rag_chat")
    assert agent.retriever is not None
    assert agent.retriever._store._tenant == "toko-a"
    job = JobController(_settings(), InMemorySaver(), Runner(),
                        pool=object(), rag=clients)
    agent2 = job._agent_for("toko-b:siti:sess-2", "rag_answer")
    assert agent2.retriever._store._tenant == "toko-b"


def test_controller_tanpa_rag_503_untuk_rag_agent():
    from app.controllers.ChatController import ChatController

    chat = ChatController(_settings(), InMemorySaver(), Runner())
    with pytest.raises(EmbeddingUnavailableError):
        chat._agent_for("toko-a:budi:sess-1", "rag_chat")


def test_controller_non_rag_tak_butuh_retriever():
    from app.controllers.ChatController import ChatController

    chat = ChatController(_settings(), InMemorySaver(), Runner())
    agent = chat._agent_for("toko-a:budi:sess-1", "example_chat")
    assert agent.retriever is None if hasattr(agent, "retriever") else True
