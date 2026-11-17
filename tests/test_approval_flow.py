"""v1.0 V1-C/1: alur approval benar (R1, R2, R6, R7, R8 + guard chat).

Semua di level core dengan repo ter-stub (tanpa Postgres); perilaku DB
nyata dikunci integration test_hitl_live.
"""

from __future__ import annotations

import pytest

from app.core.approvals import (
    ApprovalMultiInterruptError,
    ApprovalService,
    ApprovalStaleError,
)
from app.core.threads import ThreadIdentity
from app.infra import approvals as repo
from app.schemas.chat import ChatRequest
from tests._auth_helpers import test_settings as _settings


def _pending_row(**kw):
    base = {"id": 7, "workspace": "toko-a",
            "thread_id": "toko-a:budi:sess-1234", "interrupt_id": "i1",
            "agent": "example_chat", "tool_name": "process_refund",
            "action_hash": "h", "action_snapshot": {"action_requests": [{}]},
            "policy_version": "v", "status": "pending",
            "expires_at": None, "created_at": None, "decided_at": None}
    base.update(kw)
    return base


def _svc(monkeypatch, *, resumes=None, stale_calls=(), pending_count=1,
         calls=None):
    """ApprovalService dengan repo ter-stub + _assert_not_stale terskrip."""
    calls = calls if calls is not None else {}
    resumes = list(resumes or [])

    async def _expire(pool, **kw):
        return None

    async def _get(pool, **kw):
        return _pending_row()

    async def _count(pool, **kw):
        calls["count"] = calls.get("count", 0) + 1
        return pending_count

    async def _claim(*a, **kw):
        calls["claim"] = calls.get("claim", 0) + 1
        return _pending_row()

    async def _release(*a, **kw):
        calls["release"] = calls.get("release", 0) + 1
        return True

    async def _audit(*a, **kw):
        calls.setdefault("audits", []).append(kw.get("action"))
        return None

    monkeypatch.setattr(repo, "expire_stale", _expire)
    monkeypatch.setattr(repo, "get_approval", _get)
    monkeypatch.setattr(repo, "count_pending_for_thread", _count)
    monkeypatch.setattr(repo, "claim_decision", _claim)
    monkeypatch.setattr(repo, "release_claim", _release)
    monkeypatch.setattr(repo, "append_audit", _audit)

    stale_iter = iter(stale_calls)

    async def _stale(self, row):
        if next(stale_iter, False):
            raise ApprovalStaleError()

    async def _resume(self, row, *, decision, request_id):
        calls["resume_decision"] = decision
        outcome = resumes.pop(0) if resumes else {"status": "completed"}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ApprovalService, "_assert_not_stale", _stale)
    monkeypatch.setattr(ApprovalService, "_resume", _resume)
    return ApprovalService(_settings(), pool=None, checkpointer=None)


@pytest.mark.asyncio
async def test_r1_resume_gagal_lepas_klaim_dan_bisa_retry(monkeypatch):
    calls: dict = {}
    svc = _svc(monkeypatch, resumes=[RuntimeError("llm down"),
                                     {"status": "completed"}], calls=calls)
    with pytest.raises(RuntimeError):
        await svc.decide(workspace="toko-a", actor="sari", approval_id=7,
                         decision="approve")
    assert calls.get("release") == 1
    assert "approval_resume_failed" in calls.get("audits", [])
    # Retry decide kedua: klaim lagi + resume sukses (approved, tak macet).
    out = await svc.decide(workspace="toko-a", actor="sari", approval_id=7,
                           decision="approve")
    assert out["status"] == "approved" and out["resumed"] is True
    assert calls.get("claim") == 2


@pytest.mark.asyncio
async def test_r2_reject_ikut_resume_ke_cabang_tolak(monkeypatch):
    calls: dict = {}
    svc = _svc(monkeypatch, calls=calls)
    out = await svc.decide(workspace="toko-a", actor="sari", approval_id=7,
                           decision="reject", reason="tak sesuai")
    assert out["status"] == "rejected" and out["resumed"] is True
    assert out["result"] == {"status": "completed"}
    assert calls.get("resume_decision") == "reject"


@pytest.mark.asyncio
async def test_r6_stale_setelah_klaim_lepas_dan_409(monkeypatch):
    calls: dict = {}
    svc = _svc(monkeypatch, stale_calls=(False, True), calls=calls)
    with pytest.raises(ApprovalStaleError):
        await svc.decide(workspace="toko-a", actor="sari", approval_id=7,
                         decision="approve")
    assert calls.get("claim") == 1 and calls.get("release") == 1
    assert "approval_claim_released" in calls.get("audits", [])
    assert "resume_decision" not in calls  # resume tak pernah dipanggil


@pytest.mark.asyncio
async def test_r8_multi_interrupt_ditolak_sebelum_klaim(monkeypatch):
    calls: dict = {}
    svc = _svc(monkeypatch, pending_count=2, calls=calls)
    with pytest.raises(ApprovalMultiInterruptError) as ei:
        await svc.decide(workspace="toko-a", actor="sari", approval_id=7,
                         decision="approve")
    assert ei.value.status_code == 409
    assert ei.value.code == "APPROVAL_MULTI_INTERRUPT"
    assert "claim" not in calls


@pytest.mark.asyncio
async def test_r7_record_tak_pernah_menimpa_hash():
    """ON CONFLICT DO NOTHING: hash pending dipertahankan (bukti SQL)."""

    class _Cur:
        def __init__(self, script, executed):
            self.script = script
            self.executed = executed

        async def fetchone(self):
            return self.script.pop(0)

    class _Conn:
        def __init__(self, script, executed):
            self.script = script
            self.executed = executed

        async def execute(self, sql, params):
            self.executed.append(" ".join(str(sql).split()))
            return _Cur(self.script, self.executed)

    class _Ctx:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *a):
            return False

    executed: list[str] = []
    existing = {"id": 9, "status": "pending", "action_hash": "HASH-LAMA"}
    pool = type("_Pool", (), {"connection": lambda self: _Ctx(
        _Conn([None, existing], executed))})()
    row, created = await repo.record_pending(
        pool, workspace="toko-a", thread_id="t", interrupt_id="i",
        agent="a", tool_name="tool", action_hash="HASH-BARU",
        action_snapshot={}, policy_version="v", expires_in_seconds=60)
    assert created is False and row == existing  # lama dipertahankan
    assert len(executed) == 2
    assert "DO NOTHING" in executed[0]
    assert "DO UPDATE" not in executed[0]
    assert executed[1].startswith("SELECT")


@pytest.mark.asyncio
async def test_r2_guard_chat_di_thread_pending_409():
    """Chat baru di thread ber-interrupt → event error 409 (bukan input)."""
    from types import SimpleNamespace

    from app.controllers.ChatController import ChatController

    ran = []

    class _Graph:
        def __init__(self, interrupts):
            self._interrupts = interrupts

        async def aget_state(self, config):
            return SimpleNamespace(interrupts=self._interrupts)

    class _Agent:
        def __init__(self, interrupts):
            self.graph = _Graph(interrupts)

        async def stream_agent(self, *a, **k):
            ran.append(True)
            yield {"type": "result", "status": "completed"}

    ctrl = ChatController(_settings(), checkpointer=object())
    ctrl._agent_for = lambda tid, name: _Agent(interrupts=[{"id": "i1"}])
    req = ChatRequest(agent="example_chat", input="halo",
                      thread_id="toko-a:budi:sess-1")
    events = [e async for e in ctrl.stream(
        req, ThreadIdentity(workspace="toko-a", user="budi"))]
    assert ran == []  # agen tak jalan
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "THREAD_AWAITS_APPROVAL"
    assert events[-1]["status"] == 409

    # Tanpa interrupt → jalan normal.
    ctrl2 = ChatController(_settings(), checkpointer=object())
    ctrl2._agent_for = lambda tid, name: _Agent(interrupts=[])
    events2 = [e async for e in ctrl2.stream(
        req, ThreadIdentity(workspace="toko-a", user="budi"))]
    assert ran == [True]
    assert events2[-1]["type"] == "result"
