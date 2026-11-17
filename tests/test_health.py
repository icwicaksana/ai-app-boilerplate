"""Health endpoint: /health butuh JWT, /ready murah publik, /livez publik.

v1.0 S10: /health penuh (probe mahal + kaya-info) = terautentikasi;
/ready hanya probe DB cepat (LB/K8s tak bisa Bearer).
"""

from fastapi.testclient import TestClient

from tests._auth_helpers import app_with_settings, auth_headers, bearer


def test_health_butuh_token():
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.get("/api/v1/health")
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"


def test_health_ok_or_degraded_dengan_token():
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.get("/api/v1/health", headers=auth_headers())
    assert res.status_code == 200
    assert res.json()["status"] in ("ok", "degraded")


def test_ready_publik_tanpa_token():
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.get("/api/v1/ready")
    assert res.status_code in (200, 503)
    assert set(("status", "service", "version", "checks")) <= set(res.json())


def test_ready_hanya_cek_db_bukan_gateway():
    """Bukti murah: /ready tak punya kunci mahal (gateway/embed/rerank)."""
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    body = client.get("/api/v1/ready").json()
    assert set(body["checks"]) == {"db"}


def test_livez_tetap_publik():
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.get("/api/v1/livez")
    assert res.status_code == 200 and res.json()["status"] == "ok"


def test_health_butuh_auth_bukan_scope():
    """JWT valid tanpa scope apa pun tetap bisa /health (auth saja)."""
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.get("/api/v1/health", headers=bearer(scopes=()))
    assert res.status_code == 200
