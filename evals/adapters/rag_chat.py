"""Adapter eval rag_chat (V1-E): retrieve-tool → jawab tersitasi.

Model dinaskahkan: panggil search_knowledge_base lalu jawab final.
Retriever dinaskahkan per kasus. Fokus: sitasi + grounded + refusal.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


async def run(case: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents.AgentContext import AgentContext
    from app.agents.RagChatAgent import RagChatAgent
    from app.controllers.runner import Runner
    from evals.adapters._fakes import (ScriptedModel, ScriptedRetriever,
                                       chunk, eval_settings,
                                       no_evidence_result, ok_result,
                                       tool_call_msg)

    spec = case.get("input") or {}
    chunks = [chunk(f"c{i + 1}", c.get("content", "isi"),
                    c.get("source", "s.md"))
              for i, c in enumerate(spec.get("chunks", []))]
    res = (ok_result(chunks) if chunks else no_evidence_result())
    retriever = ScriptedRetriever([], res)
    script = [tool_call_msg("search_knowledge_base",
                            {"query": spec.get("question", "q?")}),
              spec.get("final", "selesai.")]
    model = ScriptedModel(script=script)
    agent = RagChatAgent(AgentContext(
        settings=eval_settings(), checkpointer=InMemorySaver(),
        runner=Runner(), session_id="eval:ragchat:1", store=None,
        retriever=retriever, skills=None))
    tools_called: list[str] = []
    status = "completed"
    final_text = ""
    with patch("app.agents.BaseChatAgent.get_llm", return_value=model):
        agent.graph = agent.build(session_id="eval:ragchat:1")
        config = {"configurable": {"thread_id": f"eval-{case['id']}"}}
        async for event in agent.stream_agent(spec.get("question", "q?"),
                                              config):
            if event.get("type") == "tool_start":
                tools_called.append(event.get("tool"))
            elif event.get("type") == "result":
                status = event.get("status", "completed")
                out = event.get("output") or {}
                final_text = out.get("reply", "") if isinstance(
                    out, dict) else str(out)
            elif event.get("type") == "error":
                status = "error"
                final_text = str(event.get("detail", ""))
    if status != "error" and not final_text:
        snap = await agent.graph.aget_state(config)
        msgs = list(getattr(snap, "values", {}).get("messages", []))
        ai_texts = [m.content for m in msgs
                    if m.__class__.__name__ == "AIMessage" and m.content]
        final_text = ai_texts[-1] if ai_texts else ""
    post = agent.postprocess(final_text)
    return {"status": status, "tools_called": tools_called,
            "final": final_text, "grounded": post.get("grounded")}
