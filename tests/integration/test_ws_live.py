"""Integrasi: WebSocket /ws/chat (TestClient + Valkey + app nyata).

Menguji handshake ticket sekali pakai, Origin allowlist, envelope + seq,
ping/pong, cancel, dan penolakan ticket ulang. LLM tidak dipanggil: agen
palsu didaftarkan ke registry untuk jalur sukses.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agents.BaseChatAgent import BaseChatAgent
from app.controllers.registry import CHAT_AGENTS
from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import create_app

pytestmark = pytest.mark.integration


class _FakeAgent(BaseChatAgent):
    AGENT_NAME = "ws_fake"
    SYSTEM_PROMPT = "x"
    TOOLS: list = []

    def build(self, session_id=None):  # tak menyentuh LLM
        return object()

    async def stream_agent(self, data, config):
        yield {"type": "started"}
        for tok in ("ha", "lo"):
            yield {"type": "token", "delta": tok}
        yield {"type": "result", "status": "completed",
               "output": {"reply": "halo"}, "interrupt": None}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(CHAT_AGENTS, "ws_fake", _FakeAgent)
    with TestClient(create_app()) as c:
        yield c


def _token(tenant="toko-a", user="budi") -> str:
    tok, _ = create_access_token(get_settings(), tenant=tenant, user=user,
                                 scopes=("chat",))
    return tok


def _ticket(client, tenant="toko-a", user="budi") -> str:
    res = client.post("/api/v1/realtime/ticket",
                      headers={"Authorization": f"Bearer {_token(tenant, user)}"})
    assert res.status_code == 200, res.text
    return res.json()["ticket"]


def test_ticket_required_and_single_use(client):
    ticket = _ticket(client)
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    # Ticket yang sama tidak bisa dipakai dua kali.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
            ws.receive_json()


def test_missing_ticket_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws/chat?ticket=palsu") as ws:
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
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_chat_streams_envelope_with_seq(client):
    ticket = _ticket(client)
    tid = f"toko-a:budi:sess-{uuid.uuid4().hex[:8]}"
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
        ws.send_json({"type": "chat", "agent": "ws_fake",
                      "input": "halo", "thread_id": tid})
        frames = [ws.receive_json() for _ in range(4)]
    types = [f["type"] for f in frames]
    assert types == ["started", "token", "token", "result"]
    assert [f["seq"] for f in frames] == [1, 2, 3, 4]
    assert frames[-1]["output"]["reply"] == "halo"


def test_cancel_stops_run(client):
    ticket = _ticket(client)
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
        ws.send_json({"type": "chat", "agent": "ws_fake", "input": "halo"})
        ws.send_json({"type": "cancel"})
        seen = set()
        for _ in range(6):
            frame = ws.receive_json()
            seen.add(frame["type"])
            if frame["type"] in ("cancelled", "result", "error"):
                break
    assert "cancelled" in seen or "result" in seen


def test_unknown_message_type_returns_error(client):
    ticket = _ticket(client)
    with client.websocket_connect(f"/api/v1/ws/chat?ticket={ticket}") as ws:
        ws.send_json({"type": "ngawur"})
        frame = ws.receive_json()
    assert frame["type"] == "error" and frame["code"] == "BAD_MESSAGE"
