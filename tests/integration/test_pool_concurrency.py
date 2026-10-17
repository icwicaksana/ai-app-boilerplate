"""50 request paralel, 1 pool: tanpa serialize-error + tanpa state bleed."""

import asyncio

import pytest
from langgraph.graph import END, START, StateGraph

pytestmark = pytest.mark.integration


def _counter_graph(checkpointer):
    def inc(state):
        return {"count": state.get("count", 0) + 1}

    g = StateGraph(dict)
    g.add_node("inc", inc)
    g.add_edge(START, "inc")
    g.add_edge("inc", END)
    return g.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_50_parallel_no_serialize_no_bleed(persistence, tag):
    _, checkpointer, _ = persistence
    graph = _counter_graph(checkpointer)
    prefix = f"it-{tag}"

    async def one(i: int):
        tid = f"{prefix}:user-{i:02d}:sess-a"
        cfg = {"configurable": {"thread_id": tid}}
        for _ in range(3):
            await graph.ainvoke({"count": i}, config=cfg)
        snap = await graph.aget_state(cfg)
        return tid, snap.values.get("count")

    try:
        results = await asyncio.gather(*[one(i) for i in range(50)])
    finally:
        for i in range(50):
            try:
                await checkpointer.adelete_thread(f"{prefix}:user-{i:02d}:sess-a")
            except Exception:
                pass
    assert len(results) == 50
    for i, (tid, count) in enumerate(results):
        assert count == i + 1, f"{tid} bleed (count={count}, harap {i + 1})"
