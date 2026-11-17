"""Integrasi: resume turn-level lewat HTTP (producer terpisah dari koneksi).

Mensimulasikan klien putus di tengah stream, lalu reconnect dengan
`Last-Event-ID` dan memastikan sisa event + terminal tetap diterima.
Memakai app + lifespan NYATA (Postgres+Valkey); hanya agen yang di-stub.
"""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.controllers.ChatController import ChatController
from app.core.config import Settings, get_settings
from app.core.dependencies import get_chat_controller
from app.main import create_app

pytestmark = pytest.mark.integration


class _FakeChat(ChatController):
    """Agen palsu: 3 token lambat lalu result (cukup untuk putus-sambung)."""

    def __init__(self, settings):
        super().__init__(settings, checkpointer=object())

    def validate_before_stream(self, req, identity):
        return "toko-a:budi:sess-1"

    async def preflight(self, req, identity):
        return "toko-a:budi:sess-1", {}, self

    async def stream(self, req, identity, *, request_id="-", agent=None):
        for i in range(3):
            await asyncio.sleep(0.15)
            yield {"thread_id": "toko-a:budi:sess-1", "request_id": request_id,
                   "type": "token", "delta": f"t{i}"}
        yield {"thread_id": "toko-a:budi:sess-1", "request_id": request_id,
               "type": "result", "status": "completed",
               "output": {"reply": "t0t1t2"}, "interrupt": None}


def _parse_sse(lines):
    """Ambil (entry_id, event) dari potongan SSE `id: ... / data: ...`."""
    out, rid = [], None
    for line in lines:
        line = line.strip()
        if line.startswith("id: "):
            rid = line[4:]
        elif line.startswith("data: "):
            out.append((rid, json.loads(line[6:])))
    return out


@pytest.fixture
def client(db_url, cache_url):
    settings = Settings(_env_file=None, DATABASE_URL=db_url, CACHE_URL=cache_url,
                        AUTH_MODE="jwt", JWT_SECRET="z" * 40, ENVIRONMENT="dev",
                        RATE_LIMIT_ENABLED=False, ENABLE_DOCS=False,
                        PHOENIX_ENABLED=False)
    app = create_app(settings)
    # Route memakai Depends(get_settings) → override agar token uji cocok.
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_chat_controller] = lambda: _FakeChat(settings)
    app.state.test_settings = settings  # dipakai helper auth
    with TestClient(app) as c:
        yield c


def _auth(client) -> dict:
    from app.core.security import create_access_token

    token, _ = create_access_token(
        client.app.state.test_settings,
        workspace="toko-a", user="budi", scopes=("chat",))
    return {"Authorization": f"Bearer {token}"}


def test_resume_after_disconnect(client):
    headers = _auth(client)
    tid = f"toko-a:budi:sess-{uuid.uuid4().hex[:8]}"
    body = {"agent": "example_chat", "input": "halo", "thread_id": tid}

    seen: list[tuple[str | None, dict]] = []
    with client.stream("POST", "/api/v1/chat/stream", json=body,
                       headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("X-Turn-ID")
        turn_id = resp.headers["X-Turn-ID"]
        for line in resp.iter_lines():
            if line.startswith("data: "):
                seen.append((None, json.loads(line[6:])))
            if len(seen) >= 2:  # putus setelah started + 1 token
                break
    started = seen[0][1]
    assert started["type"] == "started" and started["turn_id"] == turn_id

    # Tunggu producer selesai (independen dari koneksi klien).
    deadline = time.time() + 10
    while time.time() < deadline:
        with client.stream("GET", f"/api/v1/chat/stream/{turn_id}",
                           headers=headers) as r2:
            if r2.status_code == 200:
                break
        time.sleep(0.1)

    # Reconnect TANPA cursor → replay penuh (started..result).
    with client.stream("GET", f"/api/v1/chat/stream/{turn_id}",
                       headers=headers) as r2:
        assert r2.status_code == 200
        frames = _parse_sse(r2.iter_lines())
    types = [e["type"] for _, e in frames]
    assert types[0] == "started" and types[-1] == "result"
    assert types.count("token") == 3

    # Reconnect DENGAN Last-Event-ID → tidak mengulang event yang sudah dilihat.
    first_id = frames[0][0]
    with client.stream("GET", f"/api/v1/chat/stream/{turn_id}",
                       headers={**headers, "Last-Event-ID": first_id}) as r3:
        frames2 = _parse_sse(r3.iter_lines())
    assert all(rid != first_id for rid, _ in frames2)
    assert frames2[-1][1]["type"] == "result"


def test_resume_unknown_turn_returns_410(client):
    headers = _auth(client)
    res = client.get(f"/api/v1/chat/stream/{uuid.uuid4().hex[:16]}",
                     headers=headers)
    assert res.status_code == 410
    assert res.json()["code"] == "STREAM_GONE"

