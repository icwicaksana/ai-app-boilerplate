"""Profil bertahan lintas thread + lintas restart (pool baru, DB sama)."""

import pytest
from langgraph.graph import END, START, StateGraph

from app.core.config import Settings
from app.infra.memory import MemoryStore
from app.infra.postgres import (
    close_pool,
    create_pool,
    get_checkpointer,
    get_store,
    open_pool,
)

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
async def test_profile_cross_thread_and_restart(db_url, tag):
    workspace, user = f"it-{tag}", "budi"

    async def fresh_pool():
        settings = Settings(_env_file=None, DATABASE_URL=db_url,
                            DB_POOL_MIN=2, DB_POOL_MAX=10)
        pool = create_pool(settings)
        await open_pool(pool)
        return pool, get_checkpointer(pool), get_store(pool)

    # --- sesi 1: tulis profil + 1 thread ---
    pool1, cp1, store1 = await fresh_pool()
    try:
        mem1 = MemoryStore(store1, workspace, user)
        await mem1.put_profile({"nama": "Budi", "diet": "vegetarian"})
        g1 = _counter_graph(cp1)
        t1 = f"{workspace}:{user}:sess-1"
        await g1.ainvoke({"count": 41}, config={"configurable": {"thread_id": t1}})
        # lintas thread (pool sama): profil terbaca instance baru
        mem1b = MemoryStore(store1, workspace, user)
        assert (await mem1b.get_profile())["diet"] == "vegetarian"
    finally:
        await close_pool(pool1)  # simulasi restart server

    # --- sesi 2 (pool baru): profil + history utuh, thread lanjut ---
    pool2, cp2, store2 = await fresh_pool()
    try:
        mem2 = MemoryStore(store2, workspace, user)
        assert await mem2.get_profile() == {"nama": "Budi", "diet": "vegetarian"}
        g2 = _counter_graph(cp2)
        snap = await g2.aget_state({"configurable": {"thread_id": t1}})
        assert snap.values["count"] == 42
    finally:
        try:
            await cp2.adelete_thread(t1)
        except Exception:
            pass
        await close_pool(pool2)
