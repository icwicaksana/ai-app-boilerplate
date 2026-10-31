"""Kontrak route chat saat rate-limit + /ready saat DB mati (v0.4)."""

from fastapi.testclient import TestClient

from app.controllers.ChatController import ChatController
from app.core.dependencies import get_chat_controller
from tests._auth_helpers import app_with_settings, auth_headers
from tests._fake_cache import FakeCache


class _FakeChat(ChatController):
    """ChatController palsu: tak menyentuh LLM; stream 1 event lalu selesai."""

    def __init__(self, settings):
        super().__init__(settings, checkpointer=object())

    def validate_before_stream(self, req, identity):
        return "toko-a:budi:sess-1"

    async def stream(self, req, identity, *, request_id="-"):
        yield {"thread_id": "toko-a:budi:sess-1", "request_id": request_id,
               "type": "result", "status": "completed",
               "output": {"reply": "ok"}, "interrupt": None}


def _post_chat(client, headers=None):
    return client.post("/api/v1/chat/stream",
                       headers=headers or auth_headers(),
                       json={"agent": "example_chat", "input": "hi"})


def test_chat_429_is_json_not_sse():
    """Limit terlampaui SEBELUM stream mulai â†’ JSON RFC7807 (bukan SSE)."""
    app = app_with_settings(RATE_LIMIT_CHAT_PER_MIN=1)
    app.state.cache = FakeCache()
    from app.core.config import get_settings

    settings = app.dependency_overrides[get_settings]()
    ctrl = _FakeChat(settings)
    app.dependency_overrides[get_chat_controller] = lambda: ctrl
    client = TestClient(app, raise_server_exceptions=False)

    first = _post_chat(client)
    assert first.status_code == 200
    second = _post_chat(client)
    assert second.status_code == 429
    assert second.headers["content-type"].startswith("application/json")
    assert "text/event-stream" not in second.headers.get("content-type", "")
    body = second.json()
    assert body["code"] == "RATE_LIMITED" and body["status"] == 429
    assert int(second.headers["Retry-After"]) > 0


def test_ready_503_when_db_down():
    app = app_with_settings()
    app.state.pool = None
    app.state.store = None
    res = TestClient(app).get("/api/v1/ready")
    assert res.status_code == 503
    assert res.json()["status"] == "degraded"

