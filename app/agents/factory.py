"""Satu-satunya tempat merakit `AgentContext` (v0.9 F0-5).

MASALAH YANG DITUTUP
    Dulu tiga jalur merakit context sendiri-sendiri:
      * `ChatController`      -> store + retriever  (lengkap)
      * `ApprovalService`     -> TANPA store/retriever
      * `ThreadController`    -> TANPA store/retriever
    Akibatnya graph yang dibangun ulang saat resume-approval / fork punya
    skema tool BERBEDA dari graph yang di-interrupt: `search_knowledge_base`
    dan tool memori hilang, `bound_tools` jadi `[]`, dan tidak ada satu pun
    error. Tool tulis yang seharusnya butuh approval pun bisa ikut lepas.

D11 TETAP DIHORMATI — DENGAN KEJUJURAN TENTANG BATASNYA
    Degradasi tanpa retriever itu DISENGAJA untuk jalur chat: RAG mati ->
    chat tetap jalan. Jadi agen tidak boleh melempar. Yang boleh berbeda
    adalah JALUR, bukan agen — karena itu mode ketiadaannya eksplisit:

      "degrade"        -> retriever None, agen jalan apa adanya (chat D11)
      "raise_embedding"-> EmbeddingUnavailableError / 503 (jalur chat ber-RAG)
      "raise_binding"  -> AgentBindingError / 500 (resume & fork: wiring salah)

    CATATAN JUJUR (hasil review QA F2): di produksi, chat selalu memakai
    "raise_embedding" dan resume/fork selalu "raise_binding" — tidak ada
    jalur produksi yang memakai "degrade". Artinya untuk agen NEEDS_RETRIEVER
    via chat, yang berlaku adalah 503 (bukan degradasi); D11 berlaku untuk
    agen non-RAG dan konstruksi langsung di luar controller. Mode "degrade"
    dipertahankan sebagai default aman, bukan sebagai perilaku chat.
"""

from __future__ import annotations

from typing import Literal

from app.agents.AgentContext import AgentContext
from app.core.errors import AgentBindingError, EmbeddingUnavailableError

MissingRetriever = Literal["degrade", "raise_embedding", "raise_binding"]


def _workspace_of(thread_id: str | None) -> str | None:
    """Workspace dari thread_id tervalidasi; None bila tak ada/bukan format."""
    if not thread_id:
        return None
    from app.core.threads import parse_thread_id

    try:
        workspace, _, _ = parse_thread_id(thread_id)
    except Exception:
        return None
    return workspace


def build_agent_context(
    *,
    settings,
    checkpointer,
    runner,
    session_id: str | None = None,
    store=None,
    pool=None,
    rag=None,
    skills=None,
    agent_cls=None,
    missing_retriever: MissingRetriever = "degrade",
) -> AgentContext:
    """Rakit AgentContext lengkap untuk satu eksekusi agent.

    `store` adalah PostgresStore mentah (bukan MemoryStore); MemoryStore
    dibungkus per-user di sini supaya namespace terkunci ke thread ini.

    Pada mode "raise_binding" (resume/fork), store yang hilang ATAU thread_id
    yang tak valid ikut melempar — bukan hanya retriever. Tanpa ini, graph
    resume kehilangan tool memori + MEMORY_RULES secara diam-diam (kelas bug
    yang sama dengan P0-2, hanya di sumbu memori).
    """
    strict = missing_retriever == "raise_binding"
    if strict and store is None:
        raise AgentBindingError(
            "jalur resume-approval / fork thread tidak menyertakan store "
            f"(session={session_id!r}). Tanpa store, graph dibangun ulang "
            f"tanpa tool memori — skema berbeda dari graph yang di-interrupt.")
    workspace = _workspace_of(session_id)
    if strict and workspace is None:
        raise AgentBindingError(
            f"thread_id tidak valid untuk membangun context: {session_id!r}. "
            f"Jalur resume-approval / fork thread tidak boleh menebak "
            f"namespace memori.")
    memory = None
    if store is not None and workspace is not None:
        from app.infra.memory import MemoryStore

        _, user, _ = _parts(session_id)
        memory = MemoryStore(store, workspace, user)

    retriever = _retriever_for(
        agent_cls=agent_cls, settings=settings, pool=pool, rag=rag,
        thread_id=session_id, missing_retriever=missing_retriever)

    return AgentContext(
        settings=settings,
        checkpointer=checkpointer,
        runner=runner,
        session_id=session_id,
        store=memory,
        retriever=retriever,
        # v0.9 F3: skills diteruskan apa adanya — tidak ada mode raise_*.
        # Registry kosong/None = degradasi diam yang BENAR (K10): skill
        # adalah pengetahuan opsional, bukan binding identitas.
        skills=skills,
    )


def _parts(thread_id: str):
    from app.core.threads import parse_thread_id

    return parse_thread_id(thread_id)


def _retriever_for(*, agent_cls, settings, pool, rag, thread_id,
                   missing_retriever: MissingRetriever):
    """Retriever workspace-bound untuk agen NEEDS_RETRIEVER (D2, v0.7)."""
    if agent_cls is None or not getattr(agent_cls, "NEEDS_RETRIEVER", False):
        return None

    if pool is None or rag is None:
        if missing_retriever == "degrade":
            return None
        if missing_retriever == "raise_embedding":
            raise EmbeddingUnavailableError(
                "RAG tak tersedia (lifespan RAG gagal/nonaktif).")
        raise AgentBindingError(
            f"{getattr(agent_cls, '__name__', agent_cls)} membutuhkan "
            f"retriever workspace-bound, tetapi jalur ini tidak "
            f"menyediakannya (session={thread_id!r}). Jalur resume-approval "
            f"dan fork thread WAJIB menyertakan pool+rag yang sama dengan "
            f"jalur chat, kalau tidak graph dibangun ulang dengan skema tool "
            f"berbeda dari graph yang di-interrupt.")

    workspace = _workspace_of(thread_id)
    if workspace is None:
        if missing_retriever == "degrade":
            return None
        raise AgentBindingError(
            f"thread_id tidak valid untuk membangun retriever: {thread_id!r}")

    from app.infra.rag.factory import retriever_for_workspace

    return retriever_for_workspace(settings, rag, pool, workspace=workspace)
