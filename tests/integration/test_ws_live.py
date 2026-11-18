"""Integrasi: WebSocket /ws/chat (TestClient + Valkey + app nyata).

Menguji: ready frame, handshake ticket sekali pakai, Origin allowlist (CSWSH),
envelope + seq, ping/pong, cancel, BUSY, pesan ngawur, ticket dicabut,
dan batas umur koneksi (auth_expired + close 4001).
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agents.BaseChatAgent import BaseChatAgent
from app.controllers.registry import CHAT_AGENTS
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.main import create_app

pytestmark = pytest.mark.integration


class _FakeAgent(BaseChatAgent):
    AGENT_NAME = "ws_fake"
    SYSTEM_PROMPT = "x"
    TOOLS: list = []

    def build(self, session_id=None):
        return object()

    async def stream_agent(self, data, config):
        yield {"type": "started"}
        for t in ("ha", "lo"):
            yield {"type": "text", "delta": t}
        yield {"type": "result", "status": "completed",
               "output": {"reply": "halo"}, "interrupt": None}


class _SlowAgent(_FakeAgent):
    AGENT_NAME = "ws_slow"

    async def stream_agent(self, data, config):
        import asyncio

        yield {"type": "started"}
        await asyncio.sleep(2)
        yield {"type": "result", "status": "completed", "output": {}, "interrupt": None}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(CHAT_AGENTS, "ws_fake", _FakeAgent)
    monkeypatch.setitem(CHAT_AGENTS, "ws_slow", _SlowAgent)
    with TestClient(create_app()) as c:
        yield c


def _token(workspace="toko-a", user="budi") -> str:
    tok, _ = create_access_token(get_settings(), workspace=workspace, user=user,
                                 scopes=("chat",))
    return tok


def _ticket(client, workspace="toko-a", user="budi") -> str:
    res = client.post("/api/v1/realtime/ticket",
                      headers={"Authorization": f"Bearer {_token(workspace, user)}"})
    assert res.status_code == 200, res.text
    return res.json()["ticket"]


def _ready(ws) -> dict:
    frame = ws.receive_json()
    assert frame["type"] == "ready", frame
    assert frame["subprotocol"] == "ai-app.v1"
    assert frame["expires_in"] > 0 and frame["heartbeat"] > 0
    return frame


# --- handshake & auth ---

def test_ready_frame_then_ping_pong(client):
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={_ticket(client)}") as ws:
        _ready(ws)
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ticket_single_use(client):
    ticket = _ticket(client)
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
        _ready(ws)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
            ws.receive_json()


def test_missing_or_invalid_ticket_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws/chat?ticket=palsu") as ws:
            ws.receive_json()


def test_revoked_ticket_rejected(client, cache_url):
    """Ticket dihapus dari cache (mis. logout/rotasi) → handshake ditolak."""
    import asyncio
    import os

    from app.core.ws_ticket import ticket_key
    from app.infra.cache import CacheClient

    ticket = _ticket(client)

    async def _revoke() -> None:
        # Klien cache SENDIRI (loop terpisah) — jangan memakai client app
        # yang terikat event loop TestClient.
        own = CacheClient.from_url(
            os.environ.get("CACHE_URL", cache_url), socket_timeout=2.0)
        try:
            await own.delete(ticket_key(ticket))
        finally:
            await own.close()

    asyncio.run(_revoke())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
            ws.receive_json()


def test_bad_origin_rejected(client):
    ticket = _ticket(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
                f"/api/v1/ws/chat?ticket={ticket}",
                headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_allowed_origin_accepted(client):
    ticket = _ticket(client)
    with client.websocket_connect(
            f"/api/v1/ws/chat?ticket={ticket}",
            headers={"Origin": "http://localhost:3000"}) as ws:
        _ready(ws)
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


# --- streaming ---

def test_chat_streams_envelope_with_seq(client):
    tid = f"toko-a:budi:sess-{uuid.uuid4().hex[:8]}"
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={_ticket(client)}") as ws:
        _ready(ws)
        ws.send_json({"type": "chat", "agent": "ws_fake",
                      "input": "halo", "thread_id": tid})
        frames = [ws.receive_json() for _ in range(4)]
    assert [f["type"] for f in frames] == ["started", "text", "text", "result"]
    assert [f["seq"] for f in frames] == [2, 3, 4, 5]  # ready = seq 1
    assert frames[-1]["output"]["reply"] == "halo"


def test_busy_when_run_in_progress(client):
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={_ticket(client)}") as ws:
        _ready(ws)
        ws.send_json({"type": "chat", "agent": "ws_slow", "input": "lama"})
        ws.send_json({"type": "chat", "agent": "ws_fake", "input": "kedua"})
        seen = []
        for _ in range(6):
            f = ws.receive_json()
            seen.append(f)
            if f.get("code") == "BUSY":
                break
        assert any(f.get("code") == "BUSY" for f in seen), seen


def test_cancel_stops_run(client):
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={_ticket(client)}") as ws:
        _ready(ws)
        ws.send_json({"type": "chat", "agent": "ws_slow", "input": "halo"})
        ws.send_json({"type": "cancel"})
        seen = set()
        for _ in range(6):
            frame = ws.receive_json()
            seen.add(frame["type"])
            if frame["type"] in ("cancelled", "result", "error"):
                break
    assert "cancelled" in seen or "result" in seen


def test_unknown_message_type_returns_error(client):
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={_ticket(client)}") as ws:
        _ready(ws)
        ws.send_json({"type": "ngawur"})
        frame = ws.receive_json()
    assert frame["type"] == "error" and frame["code"] == "BAD_MESSAGE"


def test_chat_validation_error_is_frame_not_disconnect(client):
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={_ticket(client)}") as ws:
        _ready(ws)
        ws.send_json({"type": "chat", "agent": "ws_fake", "input": ""})
        frame = ws.receive_json()
    assert frame["type"] == "error" and frame["code"] == "VALIDATION_ERROR"


# --- batas umur koneksi ---

def test_connection_lifetime_closes_with_4001(client, monkeypatch):
    """Setelah batas umur, server kirim auth_expired lalu tutup 4001."""
    import app.api.v1.routes.ws as ws_module

    short = Settings(_env_file=None, AUTH_MODE="jwt", JWT_SECRET="z" * 40,
                     WS_MAX_CONNECTION_SECONDS=5, WS_HEARTBEAT_SECONDS=5)
    monkeypatch.setattr(ws_module, "get_settings", lambda: short)

    started = time.time()
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={_ticket(client)}") as ws:
        _ready(ws)
        frame = ws.receive_json()  # menunggu auth_expired (tanpa pesan lain)
        assert frame["type"] == "auth_expired"
        assert frame["close_code"] == 4001
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_json()
        assert ei.value.code == 4001
    assert time.time() - started >= 4
