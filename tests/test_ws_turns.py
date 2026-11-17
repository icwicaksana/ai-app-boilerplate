"""v1.0 V1-C/2: hook produsen (R3) + WS managed turn (R4) + resume-busy (R11)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.api.v1.routes.ws as ws_mod
from app.api.v1.routes.ws import _Connection
from app.core.turns import TurnManager
from app.core.ws_ticket import WsIdentity
from app.infra.turnlog import TurnLog
from tests._auth_helpers import test_settings as make_settings
from tests._fake_cache import FakeCache


def _stream(*events):
    async def gen():
        for e in events:
            await asyncio.sleep(0)
            yield e

    return gen()


@pytest.mark.asyncio
async def test_r3_hook_produsen_terima_terminal():
    """Hook dipanggil produsen untuk tiap event tersimpan (termasuk terminal).

    Ini yang menjamin approval tercatat walau SEMUA konsumen disconnect —
    konsumen (tail SSE/pump WS) tak lagi memanggil hook.
    """
    seen: list[dict] = []

    async def _hook(event):
        seen.append(event)

    mgr = TurnManager(TurnLog(FakeCache()))
    await mgr.start(workspace="t", turn_id="t1", owner="budi",
                    first_event={"type": "started"},
                    events=_stream({"type": "token"},
                                   {"type": "result",
                                    "status": "pending_approval"}),
                    on_event=_hook)
    await asyncio.gather(*list(mgr._tasks.values()))
    assert [e["type"] for e in seen] == ["token", "result"]
    assert seen[-1]["status"] == "pending_approval"


@pytest.mark.asyncio
async def test_r3_hook_gagal_tak_bunuh_turn():
    async def _boom(event):
        raise RuntimeError("hook down")

    mgr = TurnManager(TurnLog(FakeCache()))
    await mgr.start(workspace="t", turn_id="t2", owner="budi",
                    first_event={"type": "started"},
                    events=_stream({"type": "result"}), on_event=_boom)
    await asyncio.gather(*list(mgr._tasks.values()))  # tak raise
    rows = await mgr.turnlog.replay(workspace="t", turn_id="t2")
    assert [e["type"] for _, e in rows] == ["started", "result"]


def _conn(cache, *, monkeypatch, stream_events, record_calls=None):
    settings = make_settings()
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        cache=cache, turns=TurnManager(TurnLog(cache)))))
    ident = WsIdentity(workspace="toko-a", user="budi", scopes=("chat",),
                       jti="j1")
    conn = _Connection(ws, ident, settings, cache)

    class _Ctrl:
        def validate_before_stream(self, req, identity):
            return "toko-a:budi:sess-1"

        async def preflight(self, req, identity):
            return "toko-a:budi:sess-1", {}, self

        async def stream(self, req, identity, *, request_id="-", agent=None):
            for e in stream_events:
                await asyncio.sleep(0)
                yield e

    monkeypatch.setattr(ws_mod, "_controller_for", lambda w, s: _Ctrl())
    if record_calls is not None:
        async def _rec(self, event, thread_id, agent):
            record_calls.append(event)

        monkeypatch.setattr(_Connection, "_record", _rec)
    return conn


def _drain(conn):
    out = []
    while not conn.queue.empty():
        out.append(conn.queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_r4_ws_chat_managed_ack_dan_resume(monkeypatch):
    """WS chat → ack turn_id + pompa dari log + hook produsen jalan."""
    record_calls: list[dict] = []
    cache = FakeCache()
    conn = _conn(cache, monkeypatch=monkeypatch,
                 stream_events=[{"type": "result", "status": "completed"}],
                 record_calls=record_calls)
    await conn._handle({"type": "chat", "agent": "example_chat",
                        "input": "hi"})
    assert conn.run_task is not None
    await asyncio.wait_for(conn.run_task, timeout=10)
    frames = _drain(conn)
    assert frames[0]["type"] == "ack" and frames[0]["turn_id"]
    assert frames[1]["type"] == "started"  # dari turn log (replay)
    assert frames[-1]["type"] == "result"
    assert record_calls and record_calls[-1]["type"] == "result"


@pytest.mark.asyncio
async def test_r4_ws_tanpa_cache_direct_tanpa_ack(monkeypatch):
    cache = FakeCache()
    conn = _conn(cache, monkeypatch=monkeypatch,
                 stream_events=[{"type": "result", "status": "completed"}])
    conn.turns = TurnManager(TurnLog(None))  # lifespan tanpa cache
    await conn._handle({"type": "chat", "agent": "example_chat",
                        "input": "hi"})
    assert conn.run_task is not None
    await asyncio.wait_for(conn.run_task, timeout=10)
    frames = _drain(conn)
    assert [f["type"] for f in frames] == ["result"]  # direct, tanpa ack


@pytest.mark.asyncio
async def test_r11_resume_tolak_saat_run_aktif(monkeypatch):
    conn = _conn(FakeCache(), monkeypatch=monkeypatch, stream_events=[])
    busy = asyncio.create_task(asyncio.sleep(60))
    conn.run_task = busy
    try:
        await conn._handle({"type": "resume", "turn_id": "t-x"})
        frames = _drain(conn)
        assert len(frames) == 1
        assert (frames[0]["status"], frames[0]["code"]) == (409, "BUSY")
        assert conn.run_task is busy and not busy.done()
    finally:
        busy.cancel()
        try:
            await busy
        except asyncio.CancelledError:
            pass
