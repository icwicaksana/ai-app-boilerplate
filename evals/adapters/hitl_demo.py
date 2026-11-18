"""Adapter eval hitl_demo (V1-E): interrupt → resume → outcome.

Deterministik penuh (tanpa LLM) — canary tier PR.
"""

from __future__ import annotations

from typing import Any


async def run(case: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from app.agents.AgentContext import AgentContext
    from app.agents.HitlDemoAgent import HitlDemoAgent
    from app.controllers.runner import Runner
    from app.core.config import Settings

    settings = Settings(_env_file=None, LITELLM_BASE_URL="http://x",
                        LITELLM_API_KEY="x", LITELLM_MODEL="m",
                        PHOENIX_ENABLED=False)
    agent = HitlDemoAgent(AgentContext(
        settings=settings, checkpointer=InMemorySaver(), runner=Runner(),
        session_id="eval:hitl:1"))
    graph = agent.build()
    config = {"configurable": {"thread_id": f"eval-{case['id']}"}}
    state = await graph.ainvoke({"messages": []}, config=config)
    snap = await graph.aget_state(config)
    interrupts = list(getattr(snap, "interrupts", None) or [])
    if not interrupts:
        return {"approved": None, "executed": None, "outcome": None,
                "interrupted": False}
    payload = interrupts[0].value
    decision = (case.get("input") or {}).get("decision", "approve")
    final = await graph.ainvoke(
        Command(resume={"decisions": [{"type": decision}]}),
        config=config)
    out: dict[str, Any] = {
        "approved": bool(final.get("approved")),
        "executed": bool(final.get("executed")),
        "outcome": final.get("outcome"),
        "interrupted": True,
    }
    if (case.get("input") or {}).get("assert_payload"):
        out["payload"] = payload
    return out
