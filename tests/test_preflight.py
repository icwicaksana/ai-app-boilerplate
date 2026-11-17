"""v1.0 R13: preflight penuh sebelum turn + replay byte-sama.

Kontrak: gagal pre-run (resolve/agen-ada/build/guard) = JSON pre-start,
TANPA turn yatim — sama persis cache on/off. Resume memutar ulang byte
yang sama dengan pengiriman asli.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.controllers.ChatController import ChatController
from app.core.config import get_settings
from app.core.dependencies import get_chat_controller
from app.core.errors import ThreadAwaitingApprovalError, UpstreamAIError
from app.core.threads import ThreadIdentity
from app.core.turns import TurnManager
from app.infra.turnlog import TurnLog
from app.schemas.chat import ChatRequest
from tests._auth_helpers import app_with_settings, auth_headers, test_settings
from tests._fake_cache import FakeCache


class _Graph:
    def __init__(self, interrupts):
        self._interrupts = interrupts

    async def aget_state(self, config):
        return SimpleNamespace(interrupts=self._interrupts)


class _Agent:
    def __init__(self, interrupts=()):
        self.graph = _Graph(interrupts)

    async def stream_agent(self, *a, **k):
        yield {"type": "result", "status": "completed"}


def _req(**kw):
    base = {"agent": "example_chat", "input": "hi",
            "thread_id": "toko-a:budi:sess-1"}
    base.update(kw)
    return ChatRequest(**base)


def _ident():
    return ThreadIdentity(workspace="toko-a", user="budi")


@pytest.mark.asyncio
async def test_preflight_guard_409_sebelum_turn():
    ctrl = ChatController(test_settings(), checkpointer=object())
    ctrl._agent_for = lambda tid, name, clearance="public": _Agent(interrupts=[{"id": "i1"}])
    with pytest.raises(ThreadAwaitingApprovalError) as ei:
        await ctrl.preflight(_req(), _ident())
    assert (ei.value.status_code, ei.value.code) == (
        409, "THREAD_AWAITS_APPROVAL")


@pytest.mark.asyncio
async def test_preflight_build_gagal_propagate():
    ctrl = ChatController(test_settings(), checkpointer=object())

    def _boom(tid, name, clearance="public"):
        raise UpstreamAIError("llm tak terkonfigurasi")

    ctrl._agent_for = _boom
    with pytest.raises(UpstreamAIError):
        await ctrl.preflight(_req(), _ident())


@pytest.mark.asyncio
async def test_preflight_ok_dan_stream_pakai_agen_1x():
    builds: list = []
    ctrl = ChatController(test_settings(), checkpointer=object())

    def _build(tid, name, clearance="public"):
        builds.append((name, clearance))
        return _Agent()

    ctrl._agent_for = _build
    thread_id, config, agent = await ctrl.preflight(_req(), _ident())
    assert thread_id == "toko-a:budi:sess-1" and isinstance(agent, _Agent)
    events = [e async for e in ctrl.stream(_req(), _ident(), agent=agent)]
    assert events[-1]["type"] == "result"
    assert builds == [("example_chat", "public")]  # build 1x (preflight saja)


def _app(ctrl):
    app = app_with_settings()
    cache = FakeCache()
    app.state.cache = cache
    app.state.turns = TurnManager(TurnLog(cache))
    app.dependency_overrides[get_chat_controller] = lambda: ctrl
    return TestClient(app, raise_server_exceptions=False), cache


def test_route_guard_409_json_tanpa_turn():
    """Thread pending → 409 JSON (bukan SSE) + NOL turn tercipta."""
    ctrl = ChatController(test_settings(), checkpointer=object())
    ctrl._agent_for = lambda tid, name, clearance="public": _Agent(interrupts=[{"id": "i1"}])
    client, cache = _app(ctrl)
    res = client.post("/api/v1/chat/stream", headers=auth_headers(),
                      json={"agent": "example_chat", "input": "hi",
                            "thread_id": "toko-a:budi:sess-1"})
    assert res.status_code == 409
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["code"] == "THREAD_AWAITS_APPROVAL"
    assert cache.streams == {}  # tanpa turn yatim


def test_replay_byte_sama_dengan_asli():
    """Resume dari 0-0 memutar byte identik pengiriman asli."""
    ctrl = ChatController(test_settings(), checkpointer=object())
    ctrl._agent_for = lambda tid, name, clearance="public": _Agent()
    client, _ = _app(ctrl)
    h = auth_headers()
    body = {"agent": "example_chat", "input": "hi"}
    first = client.post("/api/v1/chat/stream", headers=h, json=body)
    assert first.status_code == 200
    turn_id = first.headers["X-Turn-ID"]
    replay = client.get(f"/api/v1/chat/stream/{turn_id}", headers={
        **h, "Last-Event-ID": "0-0"})
    assert replay.status_code == 200
    assert replay.text == first.text
