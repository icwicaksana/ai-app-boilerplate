"""AgentContext: satu objek wiring untuk semua agent (hilangkan data-clump).

Dulu tiap __init__ menerima 4 parameter berulang:
`settings, checkpointer, runner, session_id`. Sekarang cukup 1:
`AgentContext(settings, checkpointer, runner, session_id)`.

Controller yang merakit context (session_id = thread_id), agent tinggal pakai.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import Settings

if TYPE_CHECKING:  # hanya untuk type-check; hindari import cycle runtime
    from app.infra.memory import MemoryStore
    from app.rag.ports import BoundRetriever


@dataclass
class AgentContext:
    """Wiring agent: config + state backend + pintu LangGraph + sesi percakapan."""

    settings: Settings
    checkpointer: object
    runner: object  # Runner asli (object agar fake di test tetap sah)
    session_id: str | None = None
    store: MemoryStore | None = None  # MemoryStore per-user (chat) atau None (pipeline/test)
    retriever: BoundRetriever | None = None  # RAG terikat workspace (v0.7) atau None
    skills: object = None  # SkillRegistry boot (v0.9) atau None (mati/test)
