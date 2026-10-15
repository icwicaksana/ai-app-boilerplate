"""Runner: satu-satunya pintu ke LangGraph. Dimiliki controller, di-inject ke agent.

- Controller bikin thread_id + config.
- Agent panggil runner.run()/runner.stream() di dalam run_agent()/stream_agent().
- v0.1: stream() token-level via astream_events v2; final diambil dari
  checkpoint (aget_state, TANPA eksekusi ulang). run() tetap ainvoke (dipakai
  pipeline sync + fallback chat).
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langgraph.types import Command

from app.agents.utils.MessageUtils import split_chunk_text
from app.core.logging import get_logger
from app.helpers.response import (
    stream_error,
    stream_started,
    stream_thinking,
    stream_token,
    stream_tool_end,
    stream_tool_start,
)
from app.infra.phoenix import session_context

log = get_logger(__name__)


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
        """Token-level (v0.1): started -> token*/thinking*/tool_* -> result | error.

        - Event granular dari `graph.astream_events(..., version="v2")`.
        - Hasil final dibaca dari checkpoint (`aget_state`) — TANPA eksekusi ulang.
        - Gagal mid-stream → event error terminal (tak pernah raise, tak pernah gantung).
        - Graph tanpa `astream_events` (fake/legacy di test) → fallback single-chunk
          via run() agar kontrak `started -> result` tetap terpenuhi.
        """
        yield stream_started()
        astream_events = getattr(graph, "astream_events", None)
        if astream_events is None:
            result = await self.run(graph, state, config)
            yield {"type": "result", **result}
            return
        with session_context(_thread_of(config)):
            try:
                async for event in astream_events(state, config=config, version="v2"):
                    for out in self._map_stream_event(event):
                        yield out
            except Exception as exc:
                log.exception("runner stream gagal (thread=%s)", _thread_of(config))
                yield stream_error(detail=f"{type(exc).__name__}: {exc}")
                return
            yield await self._final_result_event(graph, config)

    @staticmethod
    def _map_stream_event(event: dict) -> list[dict[str, Any]]:
        """Petakan 1 astream_event v2 → 0..n event SSE. Tak pernah raise."""
        try:
            kind = event.get("event", "")
            if kind == "on_chat_model_stream":
                chunk = (event.get("data") or {}).get("chunk")
                if chunk is None:
                    return []
                text, thinking = split_chunk_text(chunk)
                out: list[dict[str, Any]] = []
                if text:
                    out.append(stream_token(text))
                if thinking:
                    out.append(stream_thinking(thinking))
                return out
            if kind == "on_tool_start":
                data = event.get("data") or {}
                tool_input = data.get("input")
                return [stream_tool_start(
                    str(event.get("name", "tool")),
                    tool_input if isinstance(tool_input, dict) else {},
                )]
            if kind == "on_tool_end":
                return [stream_tool_end(str(event.get("name", "tool")))]
            return []
        except Exception:
            log.exception("runner petakan stream event gagal")
            return []

    async def _final_result_event(self, graph, config: dict) -> dict[str, Any]:
        """Bangun event result dari checkpoint terakhir (tanpa eksekusi ulang)."""
        messages: list = []
        try:
            snapshot = await graph.aget_state(config)
            values = getattr(snapshot, "values", None) or {}
            if isinstance(values, dict):
                found = values.get("messages", [])
                messages = list(found) if isinstance(found, list) else []
        except Exception:
            log.exception("runner baca final state gagal (thread=%s)", _thread_of(config))
        return {"type": "result", "status": "completed", "messages": messages,
                "interrupt": None, "raw": {}}

    async def resume(self, graph, resume_value: dict, config: dict) -> dict[str, Any]:
        """Slot v1: lanjutkan graph yang terjeda via Command(resume=...)."""
        with session_context(_thread_of(config)):
            raw = await graph.ainvoke(Command(resume=resume_value), config=config)
        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        return {"status": "completed", "messages": messages, "raw": raw}
