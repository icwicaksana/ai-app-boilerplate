"""Runner: satu-satunya pintu ke LangGraph. Dimiliki controller, di-inject ke agent.

- Controller bikin thread_id + config.
- Agent panggil runner.run()/runner.stream() di dalam run_agent()/stream_agent().
- v0: HITL OFF -> run selalu `completed`. stream() single-chunk (token-level di v0.1).
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langgraph.types import Command

from app.helpers.response import stream_started
from app.infra.phoenix import session_context


def _thread_of(config: dict) -> str:
    return str((config.get("configurable") or {}).get("thread_id", "-"))


class Runner:
    async def run(self, graph, state: dict, config: dict) -> dict[str, Any]:
        # session Phoenix = thread_id (1 thread = 1 percakapan di UI).
        # Tanpa instrumentor terinstall, session_context = no-op.
        with session_context(_thread_of(config)):
            try:
                raw = await graph.ainvoke(state, config=config)
            except ValueError:
                raise

        interrupts = None
        if isinstance(raw, dict):
            interrupts = raw.get("__interrupt__") or raw.get("interrupt")
        if interrupts:
            payload = interrupts[0] if isinstance(interrupts, list) else interrupts
            value = getattr(payload, "value", payload)
            return {"status": "pending_approval", "messages": raw.get("messages", []), "interrupt": value}

        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        return {"status": "completed", "messages": messages, "raw": raw}

    async def stream(self, graph, state: dict, config: dict) -> AsyncIterator[dict[str, Any]]:
        """v0: single-chunk (1 event hasil). v0.1: ganti isi dengan token-level
        via astream/astream_events tanpa ubah agent/controller."""
        yield stream_started()
        result = await self.run(graph, state, config)
        yield {"type": "result", **result}

    async def resume(self, graph, resume_value: dict, config: dict) -> dict[str, Any]:
        """Slot v1: lanjutkan graph yang terjeda via Command(resume=...)."""
        with session_context(_thread_of(config)):
            raw = await graph.ainvoke(Command(resume=resume_value), config=config)
        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        return {"status": "completed", "messages": messages, "raw": raw}
