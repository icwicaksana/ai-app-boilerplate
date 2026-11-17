"""Integrasi: alur HITL penuh dengan graf NYATA yang interrupt.

Skenario:
1. Graf berhenti di `interrupt()` (payload gaya HITLRequest).
2. Interrupt dicatat â†’ baris approval pending + audit `approval_requested`.
3. Approve â†’ graph di-resume (decisions) â†’ selesai; audit `approval_decided`.
4. Double-decide â†’ 409; reject â†’ tidak resume; hash chain valid.
"""

import uuid
from typing import Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.AgentContext import AgentContext
from app.agents.BaseChatAgent import BaseChatAgent
from app.controllers.registry import CHAT_AGENTS
from app.controllers.runner import Runner
from app.core.approvals import (
    ApprovalService,
    ApprovalNotFoundError,
    ApprovalStateError,
)
from app.core.config import Settings
from app.core.hitl import action_hash
from app.infra import approvals as repo
import pytest_asyncio

from app.infra.postgres import (
    close_pool,
    create_pool,
    get_checkpointer,
    get_store,
    open_pool,
    setup_persistence,
)

pytestmark = pytest.mark.integration

AMOUNT = 750_000


class _S(TypedDict, total=False):
    approved: bool
    executed: bool


def _payload() -> dict[str, Any]:
    return {
        "action_requests": [{"name": "process_refund",
                             "args": {"order_id": "ORD-1", "amount": AMOUNT},
                             "description": "refund"}],
        "review_configs": [{"action_name": "process_refund",
                            "allowed_decisions": ["approve", "reject"]}],
    }


class _HitlAgent(BaseChatAgent):
    AGENT_NAME = "hitl_fake"
    SYSTEM_PROMPT = "x"
    TOOLS: list = []

    def build(self, session_id=None):
        def node(state: _S):
            decision = interrupt(_payload())
            approved = any(d.get("type") == "approve"
                           for d in (decision or {}).get("decisions", []))
            return {"approved": approved, "executed": approved}

        g = StateGraph(_S)
        g.add_node("approve", node)
        g.add_edge(START, "approve")
        g.add_edge("approve", END)
        return g.compile(checkpointer=self.checkpointer)


@pytest.fixture
def hitl(db_url, monkeypatch):
    monkeypatch.setitem(CHAT_AGENTS, "hitl_fake", _HitlAgent)
    settings = Settings(_env_file=None, DATABASE_URL=db_url,
                        GRAPH_DURABILITY="sync")
    return settings


@pytest_asyncio.fixture
async def env(hitl):
    pool = create_pool(hitl)
    await open_pool(pool)
    cp = get_checkpointer(pool)
    store = get_store(pool)
    await setup_persistence(cp, store)
    await repo.ensure_schema(pool)
    yield hitl, pool, cp, store
    await close_pool(pool)


def _service(settings, pool, cp, store) -> ApprovalService:
    return ApprovalService(settings, pool, cp,
                           runner=Runner(durability="sync"),
                           store=store)


def _graph(settings, cp, thread_id: str):
    ctx = AgentContext(settings=settings, checkpointer=cp,
                       runner=Runner(durability="sync"), session_id=thread_id)
    return _HitlAgent(ctx).graph


@pytest.mark.asyncio
async def test_full_approval_flow(env):
    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False}, 
                                 {"configurable": {"thread_id": thread_id}})
    assert first["status"] == "pending_approval"
    interrupts = first["interrupts"]

    rows = await svc.record_interrupts(workspace=workspace, thread_id=thread_id,
                                       agent="hitl_fake", interrupts=interrupts,
                                       request_id="r1")
    assert len(rows) == 1 and rows[0]["status"] == "pending"

    pending = await svc.list(workspace=workspace)
    assert [r["tool_name"] for r in pending] == ["process_refund"]
    approval_id = pending[0]["id"]
    assert pending[0]["action_hash"] == action_hash("process_refund", _payload())

    out = await svc.decide(workspace=workspace, actor="budi", approval_id=approval_id,
                           decision="approve", reason="ok", request_id="r2")
    assert out["status"] == "approved" and out["resumed"] is True
    assert out["result"]["status"] == "completed"

    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    assert snap.values.get("executed") is True

    # Double decide â†’ 409 (bukan eksekusi ganda)
    with pytest.raises(ApprovalStateError):
        await svc.decide(workspace=workspace, actor="budi",
                         approval_id=approval_id, decision="approve")

    # Audit chain utuh + mencatat aktor & keputusan
    ok, bad = await repo.verify_chain(pool, workspace=workspace)
    assert ok is True and bad is None
    entries = await repo.list_audit(pool, workspace=workspace)
    actions = [e["action"] for e in entries]
    assert actions == ["approval_requested", "approval_decided"]
    assert entries[1]["actor"] == "budi"
    assert entries[1]["payload"]["decision"] == "approve"


@pytest.mark.asyncio
async def test_reject_does_not_resume(env):
    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False},
                                 {"configurable": {"thread_id": thread_id}})
    rows = await svc.record_interrupts(workspace=workspace, thread_id=thread_id,
                                       agent="hitl_fake",
                                       interrupts=first["interrupts"])
    out = await svc.decide(workspace=workspace, actor="ani",
                           approval_id=rows[0]["id"], decision="reject",
                           reason="tidak sesuai")
    assert out["status"] == "rejected" and out["resumed"] is False

    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    assert snap.values.get("executed") in (None, False)
    assert snap.interrupts  # masih menunggu (tidak dieksekusi)


@pytest.mark.asyncio
async def test_stale_action_detected(env):
    """Aksi berubah setelah approval diminta â†’ 409 APPROVAL_STALE."""
    from app.core.approvals import ApprovalStaleError

    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False},
                                 {"configurable": {"thread_id": thread_id}})
    rows = await svc.record_interrupts(workspace=workspace, thread_id=thread_id,
                                       agent="hitl_fake",
                                       interrupts=first["interrupts"])
    # Rusak hash tersimpan â†’ mensimulasikan aksi berubah sejak approval.
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE approvals SET action_hash = %s WHERE id = %s",
            ("0" * 64, rows[0]["id"]))

    with pytest.raises(ApprovalStaleError):
        await svc.decide(workspace=workspace, actor="budi",
                         approval_id=rows[0]["id"], decision="approve")


@pytest.mark.asyncio
async def test_workspace_isolation_of_approvals(env):
    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    other = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False},
                                 {"configurable": {"thread_id": thread_id}})
    rows = await svc.record_interrupts(workspace=workspace, thread_id=thread_id,
                                       agent="hitl_fake",
                                       interrupts=first["interrupts"])
    # Workspace lain tidak bisa melihat/memutuskan approval workspace ini.
    assert await svc.list(workspace=other) == []
    with pytest.raises(ApprovalNotFoundError):
        await svc.get(workspace=other, approval_id=rows[0]["id"])


# --- edge cases v0.6 ---

async def _make_pending(svc, settings, cp, workspace, thread_id):
    graph = _graph(settings, cp, thread_id)
    first = await svc.runner.run(graph, {"approved": False},
                                 {"configurable": {"thread_id": thread_id}})
    rows = await svc.record_interrupts(workspace=workspace, thread_id=thread_id,
                                       agent="hitl_fake",
                                       interrupts=first["interrupts"])
    return rows[0]["id"]


@pytest.mark.asyncio
async def test_expired_approval_cannot_be_decided(env):
    from app.core.approvals import ApprovalExpiredError

    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    approval_id = await _make_pending(svc, settings, cp, workspace, thread_id)

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE approvals SET expires_at = now() - interval '1 minute' "
            "WHERE id = %s", (approval_id,))
    with pytest.raises(ApprovalExpiredError):
        await svc.decide(workspace=workspace, actor="budi",
                         approval_id=approval_id, decision="approve")
    row = await repo.get_approval(pool, workspace=workspace, approval_id=approval_id)
    assert row["status"] == "expired"


@pytest.mark.asyncio
async def test_unknown_approval_404_and_invalid_decision(env):
    from app.core.errors import AppError

    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    svc = _service(settings, pool, cp, store)
    with pytest.raises(ApprovalNotFoundError):
        await svc.get(workspace=workspace, approval_id=999_999)

    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    approval_id = await _make_pending(svc, settings, cp, workspace, thread_id)
    with pytest.raises(AppError) as ei:
        await svc.decide(workspace=workspace, actor="budi",
                         approval_id=approval_id, decision="mungkin")
    assert ei.value.status_code == 422 and ei.value.code == "INVALID_DECISION"


@pytest.mark.asyncio
async def test_reject_then_approve_is_409(env):
    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    approval_id = await _make_pending(svc, settings, cp, workspace, thread_id)

    await svc.decide(workspace=workspace, actor="budi", approval_id=approval_id,
                     decision="reject")
    with pytest.raises(ApprovalStateError):
        await svc.decide(workspace=workspace, actor="budi",
                         approval_id=approval_id, decision="approve")


@pytest.mark.asyncio
async def test_audit_tampering_detected(env):
    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    approval_id = await _make_pending(svc, settings, cp, workspace, thread_id)
    await svc.decide(workspace=workspace, actor="budi", approval_id=approval_id,
                     decision="approve")

    ok, bad = await repo.verify_chain(pool, workspace=workspace)
    assert ok is True and bad is None

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE audit_log SET payload = payload || '{\"tampered\":true}'::jsonb "
            "WHERE workspace = %s AND action = 'approval_decided'", (workspace,))
    ok2, bad2 = await repo.verify_chain(pool, workspace=workspace)
    assert ok2 is False and bad2 is not None


@pytest.mark.asyncio
async def test_no_pending_left_after_approval(env):
    settings, pool, cp, store = env
    workspace = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{workspace}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp, store)
    approval_id = await _make_pending(svc, settings, cp, workspace, thread_id)
    await svc.decide(workspace=workspace, actor="budi", approval_id=approval_id,
                     decision="approve")
    assert await svc.list(workspace=workspace) == []
