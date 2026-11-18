"""SSE v0.1: urutan text, error terminal, kompatibilitas consumer lama (tanpa LLM).

FakeGraph meniru subset `astream_events` v2 + `aget_state` yang dipakai Runner.
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.BaseChatAgent import BaseChatAgent
from app.controllers.runner import Runner


class _Snapshot:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    """Graph scriptable: `script` = list event v2 atau Exception untuk raise."""

    def __init__(self, script, final_messages):
        self.script = script
        self.final_messages = final_messages
        self.stream_calls = 0
        self.invoke_calls = 0

    async def astream_events(self, state, config=None, version="v2"):
        self.stream_calls += 1
        for item in self.script:
            if isinstance(item, Exception):
                raise item
            yield item

    async def aget_state(self, config):
        return _Snapshot({"messages": list(self.final_messages)})

    async def ainvoke(self, state, config=None):
        self.invoke_calls += 1
        return {"messages": list(self.final_messages)}


def _chat_chunk(text):
    return {"event": "on_chat_model_stream", "name": "ChatOpenAI",
            "data": {"chunk": AIMessage(content=text)}}


def _think_chunk(text):
    return {"event": "on_chat_model_stream", "name": "ChatOpenAI",
            "data": {"chunk": AIMessage(
                content=[{"type": "reasoning", "reasoning": text}])}}


def _run(coro):
    return asyncio.run(coro)


async def _collect(graph, state=None, config=None):
    out = []
    async for e in Runner().stream(graph, state or {"messages": []},
                                   config or {"configurable": {"thread_id": "t"}}):
        out.append(e)
    return out


def test_token_order_started_tokens_tools_result():
    script = [
        _chat_chunk("halo"),
        _chat_chunk(" dunia"),
        {"event": "on_tool_start", "name": "check_stock",
         "data": {"input": {"item_id": "ITEM-99"}}},
        {"event": "on_tool_end", "name": "check_stock", "data": {}},
        _chat_chunk("!"),
        {"event": "on_chain_start", "name": "noise", "data": {}},  # diabaikan
    ]
    final = [HumanMessage(content="hi"), AIMessage(content="halo dunia!")]
    events = _run(_collect(FakeGraph(script, final)))

    kinds = [e["type"] for e in events]
    assert kinds == ["started", "text", "text", "tool_start", "tool_end",
                     "text", "result"]
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "halo dunia!"
    assert events[3]["tool"] == "check_stock"
    assert events[3]["input"] == {"item_id": "ITEM-99"}
    assert events[4]["tool"] == "check_stock"
    result = events[-1]
    assert result["status"] == "completed"


def test_thinking_envelope_flows():
    script = [_think_chunk("mikir..."), _chat_chunk("jawab")]
    final = [AIMessage(content="jawab")]
    events = _run(_collect(FakeGraph(script, final)))
    kinds = [e["type"] for e in events]
    assert kinds == ["started", "thinking", "text", "result"]
    assert events[1]["delta"] == "mikir..."


def test_midstream_raise_yields_terminal_error_no_result():
    script = [_chat_chunk("sebelum-"), TimeoutError("llm putus")]
    events = _run(_collect(FakeGraph(script, [])))
    kinds = [e["type"] for e in events]
    assert kinds == ["started", "text", "error"]
    # v1.0 R14: TimeoutError builtin = hulu timeout (504), bukan 500 generik;
    # R12: pesan driver tak bocor (generik, bisa ditindaklanjuti).
    assert events[-1]["code"] == "UPSTREAM_TIMEOUT"
    assert events[-1]["status"] == 504
    assert "llm putus" not in events[-1]["detail"]


def test_empty_deltas_skipped():
    script = [_chat_chunk(""), _chat_chunk("ok")]
    events = _run(_collect(FakeGraph(script, [AIMessage(content="ok")])))
    assert [e["type"] for e in events] == ["started", "text", "result"]


def test_backward_compat_old_consumer():
    """Consumer v0 (filter type==result) tetap dapat tepat 1 result."""
    script = [_chat_chunk("a"), _chat_chunk("b")]
    final = [AIMessage(content="ab")]
    events = _run(_collect(FakeGraph(script, final)))
    results = [e for e in events if e.get("type") == "result"]
    assert len(results) == 1
    assert results[0]["status"] == "completed"


def test_legacy_graph_fallback_single_chunk():
    """Graph tanpa astream_events (legacy) → started -> result via run()."""

    class Legacy:
        async def ainvoke(self, state, config=None):
            return {"messages": [AIMessage(content="legacy")]}

    events = _run(_collect(Legacy()))
    assert [e["type"] for e in events] == ["started", "result"]


def test_agent_passthrough_and_no_fallback_after_error():
    """BaseChatAgent teruskan text + ubah result; tanpa fallback pasca-error."""

    class Chat(BaseChatAgent):
        AGENT_NAME = "t"
        SYSTEM_PROMPT = "x"
        TOOLS = []

    script = [_chat_chunk("a"), RuntimeError("boom")]
    graph = FakeGraph(script, [])
    ag = Chat.__new__(Chat)
    ag.runner = Runner()
    ag.graph = graph

    async def go():
        return [e async for e in ag.stream_agent("hi", {"configurable": {"thread_id": "t"}})]

    events = _run(go())
    kinds = [e["type"] for e in events]
    assert kinds == ["started", "text", "error"]  # tanpa result susulan
    assert graph.invoke_calls == 0  # fallback TIDAK jalan (cegah eksekusi 2x)
