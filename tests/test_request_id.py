"""Rantai request_id (temuan core #2 + logging #3).

- Middleware: echo header / generate → header respons.
- Error handler: instance = request_id aktual (tak pernah "-").
- setup_logging: idempoten + tolak level tak dikenal.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.logging import setup_logging
from app.main import create_app


def _client():
    return TestClient(create_app(), raise_server_exceptions=False)


def test_middleware_echoes_request_id():
    res = _client().get("/api/v1/livez", headers={"X-Request-ID": "coba-1"})
    assert res.headers["X-Request-ID"] == "coba-1"


def test_middleware_generates_request_id():
    res = _client().get("/api/v1/livez")
    rid = res.headers.get("X-Request-ID", "")
    assert len(rid) == 12 and rid != "-"


def test_error_instance_uses_request_id_not_dash():
    c = _client()
    res = c.get("/api/v1/threads/toko-a:budi:sess-1/state")
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
