"""v1.0 S8: IP klien via proxy tepercaya + matriks cakupan rate-limit."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.ratelimit import client_ip
from tests._auth_helpers import app_with_settings, auth_headers
from tests._fake_cache import FakeCache


def _req(peer: str | None, xff: str | None = None):
    client = SimpleNamespace(host=peer) if peer else None
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(client=client, headers=headers)


def _settings(**kw):
    from tests._auth_helpers import test_settings

    return test_settings(**kw)


def test_tanpa_xff_pakai_peer():
    assert client_ip(_req("1.2.3.4"), _settings()) == "1.2.3.4"


def test_xff_diabaikan_tanpa_proxy_tepercaya():
    """Default (allowlist kosong): XFF spoof tak berpengaruh."""
    req = _req("1.2.3.4", "9.9.9.9")
    assert client_ip(req, _settings()) == "1.2.3.4"


def test_proxy_tepercaya_pakai_kiri_xff():
    req = _req("10.1.2.3", "203.0.113.7, 10.1.2.3")
    s = _settings(TRUSTED_PROXY_CIDRS="10.0.0.0/8")
    assert client_ip(req, s) == "203.0.113.7"


def test_peer_tak_tepercaya_abaikan_xff():
    req = _req("198.51.100.9", "203.0.113.7")
    s = _settings(TRUSTED_PROXY_CIDRS="10.0.0.0/8")
    assert client_ip(req, s) == "198.51.100.9"


def test_cidr_invalid_fail_closed():
    req = _req("10.1.2.3", "203.0.113.7")
    s = _settings(TRUSTED_PROXY_CIDRS="bukan-cidr")
    assert client_ip(req, s) == "10.1.2.3"


def test_xff_bukan_ip_abaikan():
    req = _req("10.1.2.3", "bukan-ip")
    s = _settings(TRUSTED_PROXY_CIDRS="10.0.0.0/8")
    assert client_ip(req, s) == "10.1.2.3"


def test_peer_hilang_unknown():
    assert client_ip(_req(None), _settings()) == "unknown"


# --- matriks cakupan: endpoint baru yang dilindungi ---

def _client_with(**kw):
    app = app_with_settings(**kw)
    app.state.cache = FakeCache()
    return TestClient(app, raise_server_exceptions=False)


def test_threads_dibatasi():
    c = _client_with(RATE_LIMIT_THREADS_PER_MIN=1)
    h = auth_headers()
    url = "/api/v1/threads/toko-a:budi:sess-1/state"
    assert c.get(url, headers=h).status_code == 503  # gate lolos, pool kosong
    r2 = c.get(url, headers=h)
    assert (r2.status_code, r2.json()["code"]) == (429, "RATE_LIMITED")


def test_approvals_dibatasi():
    c = _client_with(RATE_LIMIT_APPROVALS_PER_MIN=1)
    h = auth_headers()
    assert c.get("/api/v1/approvals", headers=h).status_code == 503
    r2 = c.get("/api/v1/approvals", headers=h)
    assert (r2.status_code, r2.json()["code"]) == (429, "RATE_LIMITED")


def test_documents_retry_dibatasi_bucket_ingest():
    from app.controllers.DocumentController import DocumentController
    from app.core.dependencies import get_document_controller

    app = app_with_settings(RATE_LIMIT_INGEST_PER_MIN=1)
    app.state.cache = FakeCache()
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_document_controller] = (
        lambda: DocumentController(settings, pool=None, rag_clients=None))
    c = TestClient(app, raise_server_exceptions=False)
    h = auth_headers()
    c.post("/api/v1/ingestion_jobs/1/retry", headers=h, json={})
    r2 = c.post("/api/v1/ingestion_jobs/1/retry", headers=h, json={})
    assert (r2.status_code, r2.json()["code"]) == (429, "RATE_LIMITED")


def test_refresh_dibatasi_per_ip(monkeypatch):
    """Refresh ke-2 dalam window dari IP sama -> 429 (sebelum klaim)."""
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
    app = app_with_settings(RATE_LIMIT_REFRESH_PER_MIN=1)
    cache = FakeCache()
    app.state.cache = cache
    settings = app.dependency_overrides[get_settings]()
    ctrl = AuthController(settings, pool=None, cache=cache)
    app.dependency_overrides[auth_route.get_auth_controller] = lambda: ctrl
    c = TestClient(app, raise_server_exceptions=False)
    tokens = c.post("/api/v1/auth/login",
                    json={"workspace": "toko-a", "username": "budi",
                          "password": PASSWORD}).json()
    rt = tokens["refresh_token"]
    r1 = c.post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert r1.status_code == 200
    r2 = c.post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert (r2.status_code, r2.json()["code"]) == (429, "RATE_LIMITED")
