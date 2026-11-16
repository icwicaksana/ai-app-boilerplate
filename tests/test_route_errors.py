"""Error level route: 400/403 JSON (bukan SSE kosong) + probe pool health."""

from fastapi.testclient import TestClient

from app.controllers.ChatController import ChatController
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.dependencies import get_chat_controller
from app.main import create_app
from tests._auth_helpers import app_with_settings, auth_headers

_HEADERS = auth_headers()


def _app_with_fake_ctrl():
    app = app_with_settings()
    fake = ChatController(Settings(_env_file=None), checkpointer=object(), runner=Runner())
    app.dependency_overrides[get_chat_controller] = lambda: fake
    return app


def test_chat_bad_thread_returns_json_400_not_empty_sse():
    """Regresi P1: resolve di route → 400 JSON (bukan 200 SSE kosong)."""
    client = TestClient(_app_with_fake_ctrl(), raise_server_exceptions=False)
    res = client.post(
        "/api/v1/chat/stream", headers=_HEADERS,
        json={"agent": "example_chat", "thread_id": "thr-lama", "input": "hi"},
    )
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "INVALID_THREAD_ID"
    assert body["status"] == 400
    assert "text/event-stream" not in res.headers.get("content-type", "")


def test_chat_cross_workspace_returns_json_403():
    client = TestClient(_app_with_fake_ctrl(), raise_server_exceptions=False)
    res = client.post(
        "/api/v1/chat/stream", headers=_HEADERS,
        json={"agent": "example_chat",
              "thread_id": "toko-b:budi:sess-1", "input": "hi"},
    )
    assert res.status_code == 403
    assert res.json()["code"] == "CROSS_TENANT_DENIED"


def test_missing_token_returns_401():
    """v0.4: tanpa Bearer → 401 UNAUTHENTICATED (+ WWW-Authenticate)."""
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.post("/api/v1/chat/stream", json={"input": "hi"})
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"
    assert res.headers.get("www-authenticate", "").lower().startswith("bearer")


def test_trusted_headers_ignored_in_jwt_mode():
    """Header identitas TIDAK dipercaya lagi saat AUTH_MODE=jwt."""
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.post("/api/v1/chat/stream", json={"input": "hi"},
                      headers={"X-Tenant-ID": "toko-a", "X-User-ID": "budi"})
    assert res.status_code == 401


class _FakeConnCM:
    def __init__(self, boom=False):
        self.boom = boom

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        if self.boom:
            raise ConnectionError("db mati")
        return None


class _FakePool:
    def __init__(self, boom=False):
        self.boom = boom

    def connection(self):
        return _FakeConnCM(boom=self.boom)


def test_health_pool_probe_ok_and_down():
    app_ok = create_app()
    app_ok.state.pool = _FakePool()
    app_ok.state.store = object()
    res_ok = TestClient(app_ok).get("/api/v1/health")
    assert res_ok.status_code == 200
    assert res_ok.json()["checks"]["checkpointer"].startswith("postgres")

    app_down = create_app()
    app_down.state.pool = _FakePool(boom=True)
    app_down.state.store = object()
    res_down = TestClient(app_down).get("/api/v1/health")
    assert res_down.status_code == 200
    assert res_down.json()["checks"]["checkpointer"].startswith("down")
    assert res_down.json()["status"] == "degraded"
