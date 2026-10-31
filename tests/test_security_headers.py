"""Unit: security headers + gating docs (v0.4)."""

from fastapi.testclient import TestClient

from tests._auth_helpers import app_with_settings

BASE = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def test_security_headers_present_on_success():
    res = TestClient(app_with_settings()).get("/api/v1/livez")
    assert res.status_code == 200
    for k, v in BASE.items():
        assert res.headers.get(k) == v
    assert "permissions-policy" in res.headers
    assert "cross-origin-opener-policy" in res.headers


def test_security_headers_present_on_error():
    """Header tetap ada pada respons error (401) — bukan hanya happy path."""
    res = TestClient(app_with_settings()).get("/api/v1/auth/me")
    assert res.status_code == 401
    for k, v in BASE.items():
        assert res.headers.get(k) == v


def test_hsts_absent_in_dev():
    res = TestClient(app_with_settings()).get("/api/v1/livez")
    assert "strict-transport-security" not in res.headers


def test_hsts_present_in_prod():
    app = app_with_settings(ENVIRONMENT="prod", ENABLE_DOCS=False,
                            JWT_SECRET="p" * 40,
                            CACHE_URL="redis://:p@localhost:6380/0")
    res = TestClient(app).get("/api/v1/livez")
    assert res.headers.get("strict-transport-security", "").startswith("max-age=")


def test_docs_disabled_by_default():
    c = TestClient(app_with_settings())
    assert c.get("/docs").status_code == 404
    assert c.get("/openapi.json").status_code == 404


def test_docs_enabled_when_setting_on():
    c = TestClient(app_with_settings(ENABLE_DOCS=True))
    assert c.get("/docs").status_code == 200
    assert c.get("/openapi.json").status_code == 200
