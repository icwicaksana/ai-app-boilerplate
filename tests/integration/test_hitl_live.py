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
    yield hitl, pool, cp
    await close_pool(pool)


def _service(settings, pool, cp) -> ApprovalService:
    return ApprovalService(settings, pool, cp,
                           runner=Runner(durability="sync"))


def _graph(settings, cp, thread_id: str):
    ctx = AgentContext(settings=settings, checkpointer=cp,
                       runner=Runner(durability="sync"), session_id=thread_id)
    return _HitlAgent(ctx).graph


@pytest.mark.asyncio
async def test_full_approval_flow(env):
    settings, pool, cp = env
    tenant = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{tenant}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False}, 
                                 {"configurable": {"thread_id": thread_id}})
    assert first["status"] == "pending_approval"
    interrupts = first["interrupts"]

    rows = await svc.record_interrupts(tenant=tenant, thread_id=thread_id,
                                       agent="hitl_fake", interrupts=interrupts,
                                       request_id="r1")
    assert len(rows) == 1 and rows[0]["status"] == "pending"

    pending = await svc.list(tenant=tenant)
    assert [r["tool_name"] for r in pending] == ["process_refund"]
    approval_id = pending[0]["id"]
    assert pending[0]["action_hash"] == action_hash("process_refund", _payload())

    out = await svc.decide(tenant=tenant, actor="budi", approval_id=approval_id,
                           decision="approve", reason="ok", request_id="r2")
    assert out["status"] == "approved" and out["resumed"] is True
    assert out["result"]["status"] == "completed"

    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    assert snap.values.get("executed") is True

    # Double decide â†’ 409 (bukan eksekusi ganda)
    with pytest.raises(ApprovalStateError):
        await svc.decide(tenant=tenant, actor="budi",
                         approval_id=approval_id, decision="approve")

    # Audit chain utuh + mencatat aktor & keputusan
    ok, bad = await repo.verify_chain(pool, tenant=tenant)
    assert ok is True and bad is None
    entries = await repo.list_audit(pool, tenant=tenant)
    actions = [e["action"] for e in entries]
    assert actions == ["approval_requested", "approval_decided"]
    assert entries[1]["actor"] == "budi"
    assert entries[1]["payload"]["decision"] == "approve"


@pytest.mark.asyncio
async def test_reject_does_not_resume(env):
    settings, pool, cp = env
    tenant = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{tenant}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False},
                                 {"configurable": {"thread_id": thread_id}})
    rows = await svc.record_interrupts(tenant=tenant, thread_id=thread_id,
                                       agent="hitl_fake",
                                       interrupts=first["interrupts"])
    out = await svc.decide(tenant=tenant, actor="ani",
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

    settings, pool, cp = env
    tenant = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{tenant}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False},
                                 {"configurable": {"thread_id": thread_id}})
    rows = await svc.record_interrupts(tenant=tenant, thread_id=thread_id,
                                       agent="hitl_fake",
                                       interrupts=first["interrupts"])
    # Rusak hash tersimpan â†’ mensimulasikan aksi berubah sejak approval.
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE approvals SET action_hash = %s WHERE id = %s",
            ("0" * 64, rows[0]["id"]))

    with pytest.raises(ApprovalStaleError):
        await svc.decide(tenant=tenant, actor="budi",
                         approval_id=rows[0]["id"], decision="approve")


@pytest.mark.asyncio
async def test_tenant_isolation_of_approvals(env):
    settings, pool, cp = env
    tenant = "it-hitl-" + uuid.uuid4().hex[:6]
    other = "it-hitl-" + uuid.uuid4().hex[:6]
    thread_id = f"{tenant}:budi:sess-{uuid.uuid4().hex[:8]}"
    svc = _service(settings, pool, cp)
    graph = _graph(settings, cp, thread_id)

    first = await svc.runner.run(graph, {"approved": False},
                                 {"configurable": {"thread_id": thread_id}})
    rows = await svc.record_interrupts(tenant=tenant, thread_id=thread_id,
                                       agent="hitl_fake",
                                       interrupts=first["interrupts"])
    # Tenant lain tidak bisa melihat/memutuskan approval tenant ini.
    assert await svc.list(tenant=other) == []
    with pytest.raises(ApprovalNotFoundError):
        await svc.get(tenant=other, approval_id=rows[0]["id"])

