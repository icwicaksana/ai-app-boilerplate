"""v0.4.1: deteksi interrupt dengan graf LangGraph ASLI (bukan fake).

Bug yang ditutup: `aget_state().values` TIDAK memuat `__interrupt__`;
interrupt hidup di `StateSnapshot.interrupts`. Tanpa perbaikan, jalur
streaming selalu melaporkan `completed` padahal graf berhenti menunggu
approval.
"""

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.controllers.runner import Runner, normalize_interrupts


class _State(TypedDict, total=False):
    action: str
    amount: int
    approved: bool


def _approval_graph():
    """Graf nyata: satu node yang berhenti di interrupt()."""

    def approval(state: _State):
        decision = interrupt({"action": state["action"],
                              "amount": state["amount"]})
        return {"approved": bool(decision)}

    g = StateGraph(_State)
    g.add_node("approval", approval)
    g.add_edge(START, "approval")
    g.add_edge("approval", END)
    return g.compile(checkpointer=InMemorySaver())


def _cfg(tid: str = "t-1") -> dict:
    return {"configurable": {"thread_id": tid}}


@pytest.mark.asyncio
async def test_run_detects_interrupt_with_id():
    graph = _approval_graph()
    result = await Runner().run(
        graph, {"action": "refund", "amount": 750_000, "approved": False}, _cfg())
    assert result["status"] == "pending_approval"
    assert result["interrupt"] == {"action": "refund", "amount": 750_000}
    assert len(result["interrupts"]) == 1
    assert result["interrupts"][0]["id"]  # id wajib ada untuk resume map
    assert result["interrupts"][0]["value"]["amount"] == 750_000


@pytest.mark.asyncio
async def test_stream_detects_interrupt_from_snapshot():
    """REGRESI UTAMA: jalur streaming dulu selalu 'completed'."""
    graph = _approval_graph()
    events = [e async for e in Runner().stream(
        graph, {"action": "refund", "amount": 750_000, "approved": False}, _cfg())]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "started" and kinds[-1] == "result"
    result = events[-1]
    assert result["status"] == "pending_approval"
    assert result["interrupts"][0]["value"]["amount"] == 750_000


@pytest.mark.asyncio
async def test_resume_completes_after_approval():
    graph = _approval_graph()
    cfg = _cfg("t-2")
    first = await Runner().run(
        graph, {"action": "refund", "amount": 750_000, "approved": False}, cfg)
    interrupt_id = first["interrupts"][0]["id"]

    resumed = await Runner().resume(graph, {interrupt_id: True}, cfg)
    assert resumed["status"] == "completed"
    assert resumed["interrupts"] is None

    snap = await graph.aget_state(cfg)
    assert snap.values["approved"] is True
    assert snap.interrupts == ()


@pytest.mark.asyncio
async def test_completed_run_has_no_interrupts():
    class _Simple(TypedDict, total=False):
        value: int

    def bump(state: _Simple):
        return {"value": state.get("value", 0) + 1}

    g = StateGraph(_Simple)
    g.add_node("bump", bump)
    g.add_edge(START, "bump")
    g.add_edge("bump", END)
    graph = g.compile(checkpointer=InMemorySaver())

    result = await Runner().run(graph, {"value": 0}, _cfg("t-3"))
    assert result["status"] == "completed"
    assert result["interrupts"] is None


# --- normalisasi (murni) ---

def test_normalize_interrupts_handles_shapes():
    class _I:
        id = "abc"
        value = {"x": 1}
        response_schema = {"type": "object"}

    out = normalize_interrupts([_I()])
    assert out == [{"id": "abc", "value": {"x": 1},
                    "response_schema": {"type": "object"}}]
    assert normalize_interrupts(None) == []
    assert normalize_interrupts([]) == []
    # dict mentah (fake graph lama)
    assert normalize_interrupts({"id": "z", "value": 5})[0]["value"] == 5


def test_runner_durability_validated():
    assert Runner().durability == "async"
    assert Runner(durability="sync").durability == "sync"
    assert Runner(durability="ngawur").durability == "async"
