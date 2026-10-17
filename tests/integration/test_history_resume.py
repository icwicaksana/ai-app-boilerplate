"""Histori 100 turn: resume tepat + history API paginasi benar."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing import Annotated, TypedDict

from app.controllers.ThreadController import ThreadController
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.threads import ThreadIdentity

pytestmark = pytest.mark.integration


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]


def _echo_graph(checkpointer):
    def echo(state):
        last = state["messages"][-1].content if state["messages"] else ""
        return {"messages": [AIMessage(content=f"echo:{last}")]}

    g = StateGraph(ChatState)
    g.add_node("echo", echo)
    g.add_edge(START, "echo")
    g.add_edge("echo", END)
    return g.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_100_turns_resume_exact(persistence, tag):
    _, checkpointer, _ = persistence
    graph = _echo_graph(checkpointer)
    tid = f"it-{tag}:budi:sess-long"
    cfg = {"configurable": {"thread_id": tid}}
    try:
        for i in range(100):
            await graph.ainvoke({"messages": [HumanMessage(content=f"m{i}")]},
                                config=cfg)
        snap = await graph.aget_state(cfg)
        # 100 pasang user+echo menempel berurutan, tanpa duplikat/reset
        assert len(snap.values["messages"]) == 200
        assert snap.values["messages"][0].content == "m0"
        assert snap.values["messages"][-2].content == "m99"
        assert snap.values["messages"][-1].content == "echo:m99"
        # pasangan utuh: genap=user, gasal=echo
        for i, m in enumerate(snap.values["messages"]):
            if i % 2 == 0:
                assert isinstance(m, HumanMessage)
            else:
                assert isinstance(m, AIMessage)

        # history API di atas thread yang sama: total + paginasi konsisten
        ctrl = ThreadController(Settings(_env_file=None), checkpointer, Runner())
        ident = ThreadIdentity(tenant=f"it-{tag}", user="budi")
        full = await ctrl.get_history(ident, tid, limit=100, offset=0)
        assert full.total >= 100  # ≥1 checkpoint per turn
        assert full.items[0].messages_count == 200  # terbaru dulu
        page2 = await ctrl.get_history(ident, tid, limit=10, offset=10)
        assert len(page2.items) == 10 and page2.total == full.total
        assert page2.items[0].checkpoint_id == full.items[10].checkpoint_id
    finally:
        try:
            await checkpointer.adelete_thread(tid)
        except Exception:
            pass
