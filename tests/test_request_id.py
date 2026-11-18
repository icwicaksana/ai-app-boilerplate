"""Rantai request_id (temuan core #2 + logging #3).

- Middleware: echo header / generate → header respons.
- Error handler: instance = request_id aktual (tak pernah "-").
- setup_logging: idempoten + tolak level tak dikenal.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.logging import setup_logging
from tests._auth_helpers import app_with_settings


def _client():
    # v1.0 fix: settings DETERMINISTIK. `create_app()` tanpa argumen membaca
    # `.env` — ada di mesin dev, TIDAK ada di CI. Akibatnya tes ini dulu hijau
    # lokal (DATABASE_URL dari .env → persistence ada → 401) tapi merah di CI
    # (tanpa .env → controller 503 sebelum auth). Tes tak boleh bergantung
    # pada berkas yang tidak di-commit.
    return TestClient(app_with_settings(DATABASE_URL=""),
                      raise_server_exceptions=False)


def test_middleware_echoes_request_id():
    res = _client().get("/api/v1/livez", headers={"X-Request-ID": "coba-1"})
    assert res.headers["X-Request-ID"] == "coba-1"


def test_middleware_generates_request_id():
    res = _client().get("/api/v1/livez")
    rid = res.headers.get("X-Request-ID", "")
    assert len(rid) == 12 and rid != "-"


def test_error_instance_uses_request_id_not_dash():
    # Rute auth-only: butuh Bearer TAPI tidak butuh persistence, sehingga
    # hasilnya 401 di SEMUA lingkungan (tanpa DB, tanpa .env, Windows/Linux).
    # Rute /threads/... bergantung checkpointer → 503 saat DB tak ada.
    c = _client()
    res = c.get("/api/v1/auth/me")
    assert res.status_code == 401  # v0.4: tanpa Bearer token
    body = res.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert body["instance"] == res.headers["X-Request-ID"]
    assert body["instance"] != "-"


def test_setup_logging_rejects_unknown_level():
    with pytest.raises(ValueError):
        setup_logging("DEBGU")


def test_setup_logging_idempotent_no_handler_churn():
    import logging
    setup_logging("INFO")
    n = len(logging.getLogger().handlers)
    setup_logging("INFO")
    assert len(logging.getLogger().handlers) == n
