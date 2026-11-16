"""Kontrak SSE v0.3.1 (temuan B3/B5 + agents #1-#4).

- Agent tak dikenal / thread asing di DALAM stream → event error terminal
  (JANGAN raise: respons sudah 200).
- `started` selalu pertama; TEPAT SATU `result` | `error` di akhir.
- Interrupt dari checkpoint diteruskan (pending_approval).
- Gagal baca final → error (bukan result kosong); graph jalan-tapi-hilang
  → error tanpa eksekusi ulang.
"""

import asyncio
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.utils.MessageUtils import coerce_llm_text
from app.controllers.ChatController import ChatController
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.threads import ThreadIdentity
from app.schemas.chat import ChatRequest


def _settings():
    return Settings(_env_file=None)


def _ident():
    return ThreadIdentity(workspace="toko-a", user="budi")


def _run(coro):
    return asyncio.run(coro)


class _Snap:
    def __init__(self, values, interrupts=()):
        self.values = values
        # Realistis (v0.4.1): interrupt hidup di snapshot.interrupts,
        # BUKAN di snapshot.values.
        self.interrupts = tuple(interrupts)


class _Graph:
    """Fake graph: script event + final state + penghitung ainvoke."""

    def __init__(self, script, final_messages, interrupt=None, boom_final=False):
        self.script = script
        self.final_messages = final_messages
        self.interrupt = interrupt
        self.boom_final = boom_final
        self.ainvoke_calls = 0

    async def astream_events(self, state, config=None, version="v2"):
        for item in self.script:
            if isinstance(item, Exception):
                raise item
            yield item

    async def aget_state(self, config):
        if self.boom_final:
            raise ConnectionError("checkpoint tak terbaca")
        values = {"messages": list(self.final_messages)}
        return _Snap(values, interrupts=self.interrupt or ())

    async def ainvoke(self, state, config=None):
        self.ainvoke_calls += 1
        return {"messages": list(self.final_messages)}


def _tok(text):
    return {"event": "on_chat_model_stream", "name": "m",
            "data": {"chunk": AIMessage(content=text)}}


def _collect_chat(ctrl, req):
    async def go():
        return [e async for e in ctrl.stream(req, _ident(), request_id="r1")]
    return _run(go())


# --- B3: validasi di dalam stream tak boleh raise ---
def test_unknown_agent_yields_terminal_error():
    ctrl = ChatController(_settings(), None, Runner())
    events = _collect_chat(ctrl, ChatRequest(agent="tidak_ada", input="halo"))
    assert len(events) == 1
    err = events[0]
    assert err["type"] == "error" and err["code"] == "AGENT_NOT_FOUND"
    assert err["status"] == 404 and err["request_id"] == "r1"
    assert err["title"] == "Agent Not Found"  # T-03: judul asli, bukan generik


def test_cross_workspace_thread_yields_terminal_403():
    ctrl = ChatController(_settings(), None, Runner())
    req = ChatRequest(agent="example_chat", input="halo",
                      thread_id="toko-B:andi:sess-1")
    events = _collect_chat(ctrl, req)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert events[0]["code"] == "CROSS_TENANT_DENIED"
    assert events[0]["status"] == 403


def test_validate_before_stream_raises_json_side():
    ctrl = ChatController(_settings(), None, Runner())
    with pytest.raises(Exception):
        ctrl.validate_before_stream(
            ChatRequest(agent="tidak_ada", input="x"), _ident())
    tid = ctrl.validate_before_stream(
        ChatRequest(agent="example_chat", input="x"), _ident())
    assert tid.startswith("toko-a:budi:sess-")


# --- B5: interrupt diteruskan di rel streaming ---
def test_streaming_result_carries_interrupt():
    g = _Graph([_tok("ok")], [AIMessage(content="ok")],
               interrupt=[{"action": "refund", "amount": 600000}])
    events = _run(_collect_runner(g))
    kinds = [e["type"] for e in events]
    assert kinds == ["started", "token", "result"]
    assert events[-1]["status"] == "pending_approval"
    assert events[-1]["interrupt"] == {"action": "refund", "amount": 600000}


async def _collect_runner(graph):
    return [e async for e in Runner().stream(graph, {}, {"configurable": {}})]


# --- gagal baca final → error, bukan result kosong ---
def test_unreadable_checkpoint_yields_error_not_empty_result():
    g = _Graph([_tok("ok")], [AIMessage(content="ok")], boom_final=True)
    events = _run(_collect_runner(g))
    assert [e["type"] for e in events] == ["started", "token", "error"]
    assert g.ainvoke_calls == 0  # TANPA eksekusi ulang


# --- graph tanpa astream_events → started + result sekali jalan ---
class _InvokeOnlyGraph:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        return {"messages": [AIMessage(content="jadi")]}


def test_legacy_graph_single_chunk_contract():
    g = _InvokeOnlyGraph()
    events = _run(_collect_runner(g))
    assert [e["type"] for e in events] == ["started", "result"]
    assert g.calls == 1


# --- coerce tak bocorkan reasoning (fallback) ---
def test_coerce_fallback_hides_reasoning_blocks():
    msg = AIMessage(content=[{"type": "reasoning", "text": "rahasia chain"}])
    assert coerce_llm_text(msg.content) == ""
    mixed = AIMessage(content=[{"type": "reasoning", "text": "rahasia"},
                               {"type": "text", "text": "halo"}])
    assert coerce_llm_text(mixed.content) == "halo"


# --- _to_state tahan Decimal (dict non-serializable) ---
def test_to_state_dict_with_decimal_no_raise():
    ag = BaseChatAgent.__new__(BaseChatAgent)
    state = ag._to_state({"a": Decimal("1.5")}, "prof", "facts")
    msgs = state["messages"]
    assert any(getattr(m, "id", None) == "user-profile-v1" for m in msgs)
    assert any(getattr(m, "id", None) == "user-facts-v1" for m in msgs)
    assert "1.5" in msgs[-1].content
