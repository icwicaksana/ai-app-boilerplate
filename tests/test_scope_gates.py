"""v1.0 S2+S4: SoD approval + gate scope per-router.

SoD diuji di level core (ApprovalService.decide, semua pemanggil terlindungi);
scope diuji di level HTTP (token kurang scope -> 403 sebelum controller).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.approvals import ApprovalService
from app.core.auth import require_scope
from app.core.errors import ForbiddenError
from app.core.security import TokenClaims
from app.infra import approvals as repo
from tests._auth_helpers import app_with_settings, bearer


def _settings():
    from tests._auth_helpers import test_settings

    return test_settings()


def _pending_row(**kw):
    base = {"id": 7, "workspace": "toko-a",
            "thread_id": "toko-a:budi:sess-1234", "interrupt_id": "i1",
            "agent": "example_chat", "tool_name": "process_refund",
            "action_hash": "h", "action_snapshot": {"action_requests": [{}]},
            "policy_version": "v", "status": "pending",
            "expires_at": None, "created_at": None, "decided_at": None}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_sod_pemilik_tak_bisa_memutuskan_sendiri(monkeypatch):
    """F-A3 inti: actor == pemilik thread -> 403, klaim tak tersentuh."""
    called = {}

    async def _expire(pool, **kw):
        return None

    async def _get(pool, **kw):
        return _pending_row()

    async def _claim(*a, **kw):
        called["claim"] = True
        return _pending_row()

    monkeypatch.setattr(repo, "expire_stale", _expire)
    monkeypatch.setattr(repo, "get_approval", _get)
    monkeypatch.setattr(repo, "claim_decision", _claim)
    svc = ApprovalService(_settings(), pool=None, checkpointer=None)
    with pytest.raises(ForbiddenError):
        await svc.decide(workspace="toko-a", actor="budi", approval_id=7,
                         decision="approve")
    assert "claim" not in called


@pytest.mark.asyncio
async def test_sod_reviewer_lain_lolos(monkeypatch):
    async def _expire(pool, **kw):
        return None

    async def _get(pool, **kw):
        return _pending_row()

    async def _claim(*a, **kw):
        return _pending_row()

    async def _audit(*a, **kw):
        return None

    monkeypatch.setattr(repo, "expire_stale", _expire)
    monkeypatch.setattr(repo, "get_approval", _get)
    monkeypatch.setattr(repo, "claim_decision", _claim)
    monkeypatch.setattr(repo, "append_audit", _audit)
    svc = ApprovalService(_settings(), pool=None, checkpointer=None)

    async def _nostale(self, row):
        return None

    async def _resume(self, row, *, request_id):
        return {"status": "completed"}

    monkeypatch.setattr(ApprovalService, "_assert_not_stale", _nostale)
    monkeypatch.setattr(ApprovalService, "_resume", _resume)
    out = await svc.decide(workspace="toko-a", actor="sari", approval_id=7,
                           decision="approve")
    assert out["status"] == "approved" and out["resumed"] is True


def _claims(scopes) -> TokenClaims:
    return TokenClaims(
        subject="budi", workspace="toko-a", scopes=tuple(scopes),
        token_type="access", jti="j", expires_at=9_999_999_999,
        issued_at=1_000_000_000)


def test_require_scope_ketat_termasih_kosong():
    require_scope(_claims(("chat", "jobs")), "chat")
    with pytest.raises(ForbiddenError):
        require_scope(_claims(("chat",)), "jobs")
    with pytest.raises(ForbiddenError):
        require_scope(_claims(()), "chat")  # kosong = tolak (S4)


def _client():
    return TestClient(app_with_settings(), raise_server_exceptions=False)


def test_route_tanpa_scope_403():
    c = _client()
    # threads (butuh "threads")
    r = c.get("/api/v1/threads/toko-a:budi:sess-1/state",
              headers=bearer(scopes=("chat",)))
    assert (r.status_code, r.json()["code"]) == (403, "FORBIDDEN")
    # documents (butuh "documents")
    r = c.get("/api/v1/documents", headers=bearer(scopes=("chat",)))
    assert (r.status_code, r.json()["code"]) == (403, "FORBIDDEN")
    # approvals (butuh "approve")
    r = c.get("/api/v1/approvals", headers=bearer(scopes=("chat",)))
    assert (r.status_code, r.json()["code"]) == (403, "FORBIDDEN")
    # jobs (butuh "jobs")
    r = c.post("/api/v1/jobs/example_pipeline", json={"payload": {}},
               headers=bearer(scopes=("chat",)))
    assert (r.status_code, r.json()["code"]) == (403, "FORBIDDEN")
    # chat resume (butuh "chat")
    r = c.get("/api/v1/chat/stream/xxx", headers=bearer(scopes=("jobs",)))
    assert (r.status_code, r.json()["code"]) == (403, "FORBIDDEN")


def test_route_scope_penuh_lewat_gate():
    """Scope penuh -> BUKAN 403 (503/410 karena tanpa pool/cache di tes)."""
    c = _client()
    full = ("chat", "jobs", "threads", "approve", "documents")
    r = c.get("/api/v1/threads/toko-a:budi:sess-1/state",
              headers=bearer(scopes=full))
    assert r.status_code == 503  # gate lolos, pool tak ada
    r = c.get("/api/v1/documents", headers=bearer(scopes=full))
    assert r.status_code == 503
    r = c.get("/api/v1/approvals", headers=bearer(scopes=full))
    assert r.status_code == 503
    r = c.get("/api/v1/chat/stream/xxx", headers=bearer(scopes=full))
    assert r.status_code == 410  # gate lolos, turn tak ada


def test_scope_kosong_ditolak_di_route():
    c = _client()
    r = c.get("/api/v1/threads/toko-a:budi:sess-1/state",
              headers=bearer(scopes=()))
    assert (r.status_code, r.json()["code"]) == (403, "FORBIDDEN")
