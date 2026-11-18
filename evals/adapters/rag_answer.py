"""Adapter eval rag_answer (V1-E): node retrieve→generate→finalize.

Draft LLM dinaskahkan per kasus (fokus: gerbang grounding, bukan isi LLM).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


class _DraftLLM:
    def __init__(self, text: str):
        self._text = text

    async def ainvoke(self, prompt):
        return AIMessage(content=self._text)


async def run(case: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents.AgentContext import AgentContext
    from app.agents.RagAnswerAgent import RagAnswerAgent
    from app.controllers.runner import Runner
    from evals.adapters._fakes import (ScriptedRetriever, chunk,
                                       eval_settings, no_evidence_result,
                                       ok_result)

    spec = case.get("input") or {}
    chunks = [chunk(f"c{i + 1}", c.get("content", "isi"),
                    c.get("source", "s.md"))
              for i, c in enumerate(spec.get("chunks", []))]
    status = spec.get("status", "ok" if chunks else "no_evidence")
    res = (ok_result(chunks, degraded=spec.get("degraded", []))
           if status == "ok" else no_evidence_result())
    retriever = ScriptedRetriever([], res)
    agent = RagAnswerAgent(AgentContext(
        settings=eval_settings(), checkpointer=InMemorySaver(),
        runner=Runner(), session_id="eval:rag:1", retriever=retriever))
    agent.llm = _DraftLLM(spec.get("draft", ""))
    state: dict[str, Any] = {"question": spec.get("question", "q?")}
    state.update(await agent._node_retrieve(state))
    state.update(await agent._node_generate(state))
    out = agent._node_finalize(state)
    structured = out.get("structured") or {}
    return {"final": out.get("final"),
            "grounded": structured.get("grounded"),
            "refusal": structured.get("refusal"),
            "citations": len(structured.get("citations") or []),
            "retrieval_status": structured.get("retrieval_status")}
