"""v1.0 S9: uji chaos cache-down — matriks perilaku + alarm + header.

Dua mode cache-mati:
- `None` (by-design, CACHE_URL kosong): degradasi eksplisit per-fitur.
- `down=True` (kegagalan runtime): fail-open + ALARM + header X-Degraded.

Pengecualian fail-closed (S5): refresh + resume + ticket = 503, bukan buta.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1.routes.ws import _Connection
from app.controllers.ChatController import ChatController
from app.controllers.JobController import JobController
from app.core.config import get_settings
from app.core.degraded import degraded_counts, reset_degraded_counts
from app.core.dependencies import get_chat_controller, get_job_controller
from app.core.ws_ticket import WsIdentity
from tests._auth_helpers import app_with_settings, auth_headers
from tests._fake_cache import FakeCache


class _FakeChat(ChatController):
    def __init__(self, settings):
        super().__init__(settings, checkpointer=object())

    def validate_before_stream(self, req, identity):
        return "toko-a:budi:sess-1"

    async def preflight(self, req, identity):
        return "toko-a:budi:sess-1", {}, self

    async def stream(self, req, identity, *, request_id="-", agent=None):
        yield {"thread_id": "toko-a:budi:sess-1", "request_id": request_id,
               "type": "result", "status": "completed",
               "output": {"reply": "ok"}, "interrupt": None}


class _CountingJob(JobController):
    def __init__(self, settings):
        super().__init__(settings, checkpointer=object())
        self.calls = 0

    def _agent_for(self, thread_id, agent_name):
        ctrl = self

        class _Agent:
            async def run_agent(self, payload, config):
                ctrl.calls += 1
                return {"status": "completed", "output": {"reply": "ok"}}

        return _Agent()


@pytest.fixture(autouse=True)
def _reset_counts():
    reset_degraded_counts()
    yield
    reset_degraded_counts()


def _client(cache, **kw):
    from app.core.turns import TurnManager
    from app.infra.turnlog import TurnLog

    app = app_with_settings(**kw)
    app.state.cache = cache
    # Tiru lifespan produksi: turns dibangun dari cache yang SAMA
    # (get_turn_manager membaca state.turns, bukan state.cache).
    app.state.turns = TurnManager(TurnLog(cache))
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_chat_controller] = lambda: _FakeChat(settings)
    app.dependency_overrides[get_job_controller] = lambda: _CountingJob(settings)
    return TestClient(app, raise_server_exceptions=False)


def _degraded_of(res) -> set[str]:
    raw = res.headers.get("X-Degraded", "")
    return {p for p in raw.split(",") if p}


def test_chat_cache_down_tetap_200_dan_header():
    """Chat selamat dari cache-down (fallback direct) + header lengkap."""
    c = _client(_down())
    res = c.post("/api/v1/chat/stream", headers=auth_headers(),
                 json={"agent": "example_chat", "input": "hi"})
    assert res.status_code == 200
    assert "data:" in res.text  # SSE direct, bukan JSON error
    assert {"denylist", "rate_limit", "turns"} <= _degraded_of(res)
    counts = degraded_counts()
    assert counts.get("denylist", 0) >= 1
    assert counts.get("rate_limit", 0) >= 1
    assert counts.get("turns", 0) >= 1


def test_chat_tanpa_cache_200_tanpa_flag_turns():
    """Cache=None = by-design: direct tanpa alarm turns (bukan kegagalan)."""
    c = _client(None)
    res = c.post("/api/v1/chat/stream", headers=auth_headers(),
                 json={"agent": "example_chat", "input": "hi"})
    assert res.status_code == 200
    assert "turns" not in _degraded_of(res)
    assert "turns" not in degraded_counts()


def test_chat_cache_sehat_tanpa_header():
    c = _client(FakeCache())
    res = c.post("/api/v1/chat/stream", headers=auth_headers(),
                 json={"agent": "example_chat", "input": "hi"})
    assert res.status_code == 200
    assert "X-Degraded" not in res.headers
    assert degraded_counts() == {}


def test_resume_cache_down_503_bukan_410():
    c = _client(_down())
    res = c.get("/api/v1/chat/stream/apa-saja", headers=auth_headers())
    assert res.status_code == 503
    assert res.json()["code"] == "STORAGE_UNAVAILABLE"


def test_jobs_idempotency_down_eksekusi_tanpa_dedup():
    c = _client(_down())
    h = dict(auth_headers())
    h["Idempotency-Key"] = "k-chaos-1"
    r1 = c.post("/api/v1/jobs/example_pipeline", headers=h,
                json={"payload": {}})
    assert r1.status_code == 200
    assert "idempotency" in _degraded_of(r1)
    # Tanpa dedup: kunci sama dieksekusi LAGI (bukan replay) — didokumentasi.
    r2 = c.post("/api/v1/jobs/example_pipeline", headers=h,
                json={"payload": {}})
    assert r2.status_code == 200
    assert "Idempotent-Replayed" not in r2.headers
    assert degraded_counts().get("idempotency", 0) >= 1


def test_ticket_cache_down_503():
    c = _client(_down())
    res = c.post("/api/v1/realtime/ticket", headers=auth_headers())
    assert res.status_code == 503
    assert res.json()["code"] == "STORAGE_UNAVAILABLE"


def test_ticket_tanpa_cache_503():
    c = _client(None)
    res = c.post("/api/v1/realtime/ticket", headers=auth_headers())
    assert res.status_code == 503


def test_refresh_cache_down_503(monkeypatch):
    """Refresh fail-closed (S5) — chaos membuktikan dari sisi HTTP."""
    import app.api.v1.routes.auth as auth_route
    from app.controllers.AuthController import AuthController
    from app.core.security import hash_password
    from app.infra import users as users_repo

    PASSWORD = "pw-kuat-123"
    HASH = hash_password(PASSWORD)

    async def _get_user(pool, workspace, username):
        return {"id": 1, "workspace": "toko-a", "username": "budi",
                "password_hash": HASH, "scopes": "chat", "is_active": True}

    async def _mark_login(pool, user_id):
        return None

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", _mark_login)
    cache = FakeCache()
    app = app_with_settings()
    app.state.cache = cache
    settings = app.dependency_overrides[get_settings]()
    ctrl = AuthController(settings, pool=None, cache=cache)
    app.dependency_overrides[auth_route.get_auth_controller] = lambda: ctrl
    c = TestClient(app, raise_server_exceptions=False)
    tokens = c.post("/api/v1/auth/login",
                    json={"workspace": "toko-a", "username": "budi",
                          "password": PASSWORD}).json()
    cache.down = True  # chaos: cache mati SETELAH login
    res = c.post("/api/v1/auth/refresh",
                 json={"refresh_token": tokens["refresh_token"]})
    assert res.status_code == 503
    assert res.json()["code"] == "STORAGE_UNAVAILABLE"


def test_logout_cache_down_best_effort_204(monkeypatch):
    from app.controllers.AuthController import AuthController
    from app.core.security import hash_password
    from app.infra import users as users_repo
    import app.api.v1.routes.auth as auth_route

    PASSWORD = "pw-kuat-123"
    HASH = hash_password(PASSWORD)

    async def _get_user(pool, workspace, username):
        return {"id": 1, "workspace": "toko-a", "username": "budi",
                "password_hash": HASH, "scopes": "chat", "is_active": True}

    async def _mark_login(pool, user_id):
        return None

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", _mark_login)
    cache = FakeCache()
    app = app_with_settings()
    app.state.cache = cache
    settings = app.dependency_overrides[get_settings]()
    ctrl = AuthController(settings, pool=None, cache=cache)
    app.dependency_overrides[auth_route.get_auth_controller] = lambda: ctrl
    c = TestClient(app, raise_server_exceptions=False)
    tokens = c.post("/api/v1/auth/login",
                    json={"workspace": "toko-a", "username": "budi",
                          "password": PASSWORD}).json()
    cache.down = True
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    res = c.post("/api/v1/auth/logout", headers=h, json={})
    assert res.status_code == 204
    assert degraded_counts().get("revoke", 0) >= 1


@pytest.mark.asyncio
async def test_ws_gate_down_cache_terbuka_beralarm():
    from types import SimpleNamespace

    from tests._auth_helpers import test_settings as make_settings

    cache = _down()
    ws = SimpleNamespace(app=SimpleNamespace(
        state=SimpleNamespace(cache=cache, turns=None)))
    ident = WsIdentity(workspace="toko-a", user="budi", scopes=("chat",),
                       jti="j1")
    conn = _Connection(ws, ident, make_settings(), cache)
    assert await conn._gate("chat") is None  # fail-open
    assert degraded_counts().get("rate_limit", 0) >= 1


def _down() -> FakeCache:
    cache = FakeCache()
    cache.down = True
    return cache
