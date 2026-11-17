"""Pin kontrak beku (pengganti test tautologis, temuan F-08).

Setiap kontrak beku roadmap §registry: bentuk diuji di sini. Ubah file ini =
ubah kontrak = butuh bump major + migration note.
"""

import re

from app.core.threads import THREAD_RE


def test_health_schema_shape():
    from fastapi.testclient import TestClient

    from tests._auth_helpers import app_with_settings, auth_headers

    client = TestClient(app_with_settings())
    body = client.get("/api/v1/health", headers=auth_headers()).json()
    assert set(("status", "service", "version", "checks")) <= set(body)
    assert set(("llm", "gateway", "checkpointer", "store", "hitl")) <= set(
        body["checks"])
    assert body["version"].startswith("v")


def test_livez_is_cheap_liveness():
    """T-04: /livez tanpa probe — selalu 200 bila proses hidup."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    res = TestClient(create_app()).get("/api/v1/livez")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_sse_envelope_sequence_contract():
    """started -> token*/thinking*/tool_* -> result|error; terminator=close."""
    from app.helpers import response as R

    kinds = ["started"] + ["token", "thinking", "tool_start", "tool_end"]
    made = [R.stream_started(), R.stream_token("a"), R.stream_thinking("b"),
            R.stream_tool_start("t", {}), R.stream_tool_end("t")]
    assert [m["type"] for m in made] == kinds
    ok = R.stream_success(output={"reply": "x"})
    assert ok["type"] == "result" and ok["output"] == {"reply": "x"}
    err = R.stream_error()
    assert err["type"] == "error" and err["status"] == 500
    assert err["code"] == "STREAM_FAILED"  # RFC7807 + code extension


def test_rfc7807_error_shape():
    from app.helpers.response import job_error

    body = job_error(title="T", detail="D", status=404, code="X", instance="r")
    assert body == {"type": "about:blank", "title": "T", "status": 404,
                    "detail": "D", "instance": "r", "code": "X"}


def test_thread_regex_frozen():
    assert THREAD_RE.match("toko-a:budi:sess-7f3a2c1e")
    for bad in ("thr-xxx", "TOKO-A:budi:sess-1", "a:b:c", "toko-a:budi:",
                "toko-a:budi:sess 1"):
        assert not THREAD_RE.match(bad), bad


def test_job_response_defaults():
    from app.schemas.job import JobRequest, JobResponse

    assert JobRequest(payload={"a": 1}).thread_id is None
    job = JobResponse(thread_id="t", output={"reply": "x"})
    assert job.status == "completed" and job.meta is not None
