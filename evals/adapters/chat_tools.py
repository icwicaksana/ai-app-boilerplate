"""Adapter eval chat-tools (V1-E): example_chat + model naskah.

Rute tool-call deterministik: LLM dinaskahkan memanggil tool, hasilnya
dieksekusi nyata (tool demo murni) lalu dijawab. Fokus: tool terpanggil
dengan argumen benar + jawaban final mengandung hasil tool.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


async def run(case: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents.AgentContext import AgentContext
    from app.agents.ExampleChatAgent import ExampleChatAgent
    from app.controllers.runner import Runner
    from evals.adapters._fakes import (ScriptedModel, eval_settings,
                                       tool_call_msg)

    spec = case.get("input") or {}
    script = [tool_call_msg(spec["tool"], spec.get("args", {})),
              spec.get("final", "selesai.")]
    model = ScriptedModel(script=script)
    agent = ExampleChatAgent(AgentContext(
        settings=eval_settings(), checkpointer=InMemorySaver(),
        runner=Runner(), session_id="eval:tools:1", store=None,
        retriever=None, skills=None))
    tools_called: list[dict] = []
    final_text = ""
    status = "completed"
    with patch("app.agents.BaseChatAgent.get_llm", return_value=model):
        agent.graph = agent.build(session_id="eval:tools:1")
        config = {"configurable": {"thread_id": f"eval-{case['id']}"}}
        async for event in agent.stream_agent(spec.get("user", "halo"),
                                              config):
            if event.get("type") == "tool_start":
                tools_called.append(
                    {"name": event.get("tool"),
                     "args": event.get("input") or {}})
            elif event.get("type") == "result":
                status = event.get("status", "completed")
            elif event.get("type") == "error":
                status = "error"
                final_text = str(event.get("detail", ""))
    if status != "error":
        snap = await agent.graph.aget_state(config)
        msgs = list(getattr(snap, "values", {}).get("messages", []))
        ai_texts = [m.content for m in msgs
                    if m.__class__.__name__ == "AIMessage" and m.content]
        final_text = ai_texts[-1] if ai_texts else ""
    return {"status": status, "tools_called": tools_called,
            "final": final_text}
