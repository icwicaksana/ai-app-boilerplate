"""HTTP-level hardening: 429 rate-limit, idempotency replay/409, 413 payload.

Memakai FakeCache (bukan fakeredis) karena `incr_window` butuh Lua.
"""

from fastapi.testclient import TestClient

from app.controllers.JobController import JobController
from app.core.config import get_settings
from app.core.dependencies import get_job_controller
from tests._auth_helpers import app_with_settings, auth_headers
from tests._fake_cache import FakeCache


class _CountingController(JobController):
    """JobController palsu: hitung eksekusi, tanpa LLM/DB."""

    def __init__(self, settings):
        super().__init__(settings, checkpointer=object())
        self.calls = 0

    def _agent_for(self, thread_id, agent_name):
        ctrl = self

        class _Agent:
            async def run_agent(self, payload, config):
                ctrl.calls += 1
                return {"status": "completed",
                        "output": {"reply": f"ok:{payload.get('n')}"}}

        return _Agent()


def _client(**settings_kw):
    app = app_with_settings(**settings_kw)
    app.state.cache = FakeCache()
    settings = app.dependency_overrides[get_settings]()
    ctrl = _CountingController(settings)
    app.dependency_overrides[get_job_controller] = lambda: ctrl
    return TestClient(app, raise_server_exceptions=False), ctrl


def _post(client, *, payload=None, key=None, headers=None):
    h = dict(headers or auth_headers())
    if key:
        h["Idempotency-Key"] = key
    return client.post("/api/v1/jobs/example_pipeline",
                       headers=h, json={"payload": payload or {"n": 1}})


# --- rate limit ---

def test_jobs_rate_limited_after_limit():
    client, ctrl = _client(RATE_LIMIT_JOBS_PER_MIN=2)
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200
    blocked = _post(client)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "RATE_LIMITED" and body["status"] == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert blocked.headers["X-RateLimit-Limit"] == "2"
    assert ctrl.calls == 2  # yang ditolak TIDAK dieksekusi


def test_rate_limit_isolated_per_user():
    client, ctrl = _client(RATE_LIMIT_JOBS_PER_MIN=1)
    assert _post(client).status_code == 200
    assert _post(client).status_code == 429
    other = _post(client, headers=auth_headers(user="ani"))
    assert other.status_code == 200 and ctrl.calls == 2


def test_rate_limit_disabled_allows_all():
    client, ctrl = _client(RATE_LIMIT_JOBS_PER_MIN=1, RATE_LIMIT_ENABLED=False)
    for _ in range(3):
        assert _post(client).status_code == 200
    assert ctrl.calls == 3


def test_rate_limit_fail_open_when_cache_down():
    client, ctrl = _client(RATE_LIMIT_JOBS_PER_MIN=1)
    client.app.state.cache.down = True  # type: ignore[attr-defined]
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200
    assert ctrl.calls == 2


# --- idempotency ---

def test_double_submit_executes_once():
    client, ctrl = _client(RATE_LIMIT_ENABLED=False)
    first = _post(client, key="k-1")
    second = _post(client, key="k-1")
    assert first.status_code == 200 and second.status_code == 200
    assert ctrl.calls == 1  # HANYA satu eksekusi
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert first.headers.get("Idempotent-Replayed") is None
    assert second.json()["output"] == first.json()["output"]


def test_same_key_different_payload_conflict():
    client, ctrl = _client(RATE_LIMIT_ENABLED=False)
    assert _post(client, key="k-2", payload={"n": 1}).status_code == 200
    res = _post(client, key="k-2", payload={"n": 2})
    assert res.status_code == 409
    assert res.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert ctrl.calls == 1


def test_different_keys_execute_twice():
    client, ctrl = _client(RATE_LIMIT_ENABLED=False)
    assert _post(client, key="a").status_code == 200
    assert _post(client, key="b").status_code == 200
    assert ctrl.calls == 2


def test_without_key_always_executes():
    client, ctrl = _client(RATE_LIMIT_ENABLED=False)
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200
    assert ctrl.calls == 2


def test_failed_execution_releases_key(monkeypatch):
    client, ctrl = _client(RATE_LIMIT_ENABLED=False)

    async def _boom(self, payload, config):
        raise RuntimeError("gagal")

    monkeypatch.setattr(ctrl, "_agent_for",
                        lambda tid, name: type("A", (), {
                            "run_agent": _boom})())
    first = _post(client, key="k-3")
    assert first.status_code == 500
    # Kunci dilepas → retry dengan kunci sama boleh jalan (bukan 409).
    second = _post(client, key="k-3")
    assert second.status_code == 500  # masih gagal, tapi bukan 409


# --- payload cap ---

def test_payload_too_large_413():
    client, ctrl = _client(RATE_LIMIT_ENABLED=False, MAX_JOB_PAYLOAD_BYTES=1024)
    res = _post(client, payload={"blob": "x" * 5000})
    assert res.status_code == 413
    assert res.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert ctrl.calls == 0


def test_payload_within_cap_ok():
    client, ctrl = _client(RATE_LIMIT_ENABLED=False, MAX_JOB_PAYLOAD_BYTES=4096)
    assert _post(client, payload={"blob": "x" * 100}).status_code == 200
    assert ctrl.calls == 1
