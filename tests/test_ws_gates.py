"""v1.0 S6+S7: gate WS (scope+rate-limit+revoke) + wiring controller == HTTP."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fakeredis.aioredis import FakeRedis

from app.api.v1.routes.ws import _Connection, _controller_for
from app.core.errors import StorageUnavailableError
from app.core.ws_ticket import TicketStore, WsIdentity
from app.infra.cache import CacheClient, deny_key
from tests._auth_helpers import test_settings as make_settings
from tests._fake_cache import FakeCache


def _ws_state(**kw):
    base = {"checkpointer": object(), "store": object(), "pool": object(),
            "rag": object(), "skills": object(), "cache": None, "turns": None}
    base.update(kw)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**base)))


def _conn(scopes=("chat",), jti="j1", cache=None, **settings_kw):
    settings = make_settings(**settings_kw)
    ws = _ws_state(cache=cache)
    ident = WsIdentity(workspace="toko-a", user="budi", scopes=scopes, jti=jti)
    return _Connection(ws, ident, settings, cache)


def test_controller_for_wires_pool_rag_like_http():
    ws = _ws_state()
    ctrl = _controller_for(ws, make_settings())
    assert ctrl.checkpointer is ws.app.state.checkpointer
    assert ctrl.pool is ws.app.state.pool
    assert ctrl.rag is ws.app.state.rag
    assert ctrl.skills is ws.app.state.skills


def test_controller_for_missing_persistence_503():
    ws = _ws_state(checkpointer=None, store=None)
    with pytest.raises(StorageUnavailableError):
        _controller_for(ws, make_settings())


@pytest.mark.asyncio
async def test_gate_tanpa_scope_chat_403():
    conn = _conn(scopes=("jobs",))
    err = await conn._gate("chat")
    assert err is not None and (err["status"], err["code"]) == (403, "FORBIDDEN")
    err = await conn._gate("resume")
    assert err is not None and err["status"] == 403


@pytest.mark.asyncio
async def test_handle_tanpa_scope_tak_buat_run():
    conn = _conn(scopes=())
    await conn._handle({"type": "chat", "agent": "example_chat",
                        "input": "hi"})
    assert conn.run_task is None
    frame = conn.queue.get_nowait()
    assert (frame["status"], frame["code"]) == (403, "FORBIDDEN")


@pytest.mark.asyncio
async def test_gate_rate_limit_429():
    # FakeCache (incr_window Python): fakeredis tak bisa EVAL/Lua.
    conn = _conn(cache=FakeCache(), WS_CHAT_PER_MIN=1)
    assert await conn._gate("chat") is None
    err = await conn._gate("chat")
    assert err is not None and (err["status"], err["code"]) == (429, "RATE_LIMITED")
    assert err["retry_after"] >= 1


@pytest.mark.asyncio
async def test_gate_tanpa_cache_fail_open():
    conn = _conn(cache=None)
    assert await conn._gate("chat") is None


@pytest.mark.asyncio
async def test_auth_still_valid_logout_memutus():
    cache = CacheClient(FakeRedis(decode_responses=True))
    conn = _conn(cache=cache, jti="j-logout")
    assert await conn._auth_still_valid() is True
    await cache.set_json(deny_key("j-logout"), {"typ": "access"},
                         ttl_seconds=60)
    assert await conn._auth_still_valid() is False


@pytest.mark.asyncio
async def test_auth_still_valid_tanpa_jti_aman():
    conn = _conn(cache=None, jti=None)
    assert await conn._auth_still_valid() is True


@pytest.mark.asyncio
async def test_ticket_bawa_scopes_dan_jti():
    store = TicketStore(FakeCache(), ttl_seconds=30)
    ticket, _ = await store.issue(workspace="toko-a", user="budi",
                                  scopes=("chat", "jobs"), jti="j9")
    ident = await store.consume(ticket)
    assert ident is not None
    assert ident.scopes == ("chat", "jobs") and ident.jti == "j9"
    # tiket lama (tanpa field baru) tetap terbaca sebagai kosong
    ticket2, _ = await store.issue(workspace="toko-a", user="budi")
    ident2 = await store.consume(ticket2)
    assert ident2 is not None
    assert ident2.scopes == () and ident2.jti is None
