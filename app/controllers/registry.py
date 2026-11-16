"""Registry agen: tambah AI App baru = 1 baris di sini, tanpa edit controller.

- Rel chat (streaming SSE): CHAT_AGENTS — dipakai ChatController.
- Rel pipeline (sync): JOB_AGENTS — dipakai JobController.
- Template (*TemplateChatAgent / *TemplatePipelineAgent) SENGAJA tidak
  didaftarkan: itu pedoman copy-paste, bukan agen hidup (pipeline template
  bahkan abstract — tak bisa di-instance).

Contoh tambah agen chat baru:
    from app.agents.CsAgent import CsAgent
    CHAT_AGENTS = {"example_chat": ExampleChatAgent, "cs": CsAgent}
"""

from __future__ import annotations

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.BasePipelineAgent import BasePipelineAgent
from app.agents.ExampleChatAgent import ExampleChatAgent
from app.agents.ExamplePipelineAgent import ExamplePipelineAgent
from app.agents.HitlDemoAgent import HitlDemoAgent
from app.agents.RagAnswerAgent import RagAnswerAgent
from app.agents.RagChatAgent import RagChatAgent

CHAT_AGENTS: dict[str, type[BaseChatAgent]] = {
    "example_chat": ExampleChatAgent,
    # Demo HITL tanpa LLM (v0.6): mencoba alur approval end-to-end.
    "hitl_demo": HitlDemoAgent,
    # RAG chat (v0.7): ReAct + search_knowledge_base workspace-bound.
    "rag_chat": RagChatAgent,
}

JOB_AGENTS: dict[str, type[BasePipelineAgent]] = {
    "example_pipeline": ExamplePipelineAgent,
    # RAG satu tembakan (v0.7): retrieve → generate → grounding gate.
    "rag_answer": RagAnswerAgent,
}
