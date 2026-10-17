"""Injeksi profil + tulis heuristik di stream_agent (tanpa DB/LLM).

FakeGraph meniru subset Runner: astream_events v2 + aget_state.
FakeMemory meniru MemoryStore: get_profile/put_profile/put_fact.
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.BaseChatAgent import BaseChatAgent
from app.controllers.runner import Runner


class _Snapshot:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    def __init__(self, script, final_messages):
        self.script = script
        self.final_messages = final_messages
        self.seen_states: list = []

    async def astream_events(self, state, config=None, version="v2"):
        self.seen_states.append(state)
        for item in self.script:
            if isinstance(item, Exception):
                raise item
            yield item

    async def aget_state(self, config):
        return _Snapshot({"messages": list(self.final_messages)})

    async def ainvoke(self, state, config=None):
        self.seen_states.append(state)
        return {"messages": list(self.final_messages)}


class FakeMemory:
    def __init__(self, profile=None, boom=False, fact_hits=None):
        self.profile = dict(profile or {})
        self.boom = boom
        self.fact_hits = list(fact_hits or [])
        self.facts: list = []
        self.profile_writes: list = []

    async def get_profile(self):
        if self.boom:
            raise ConnectionError("db mati")
        return dict(self.profile)

    async def search_facts(self, query="", limit=5):
        if self.boom:
            raise ConnectionError("db mati")
        return list(self.fact_hits)[:limit]

    async def put_profile(self, patch):
        if self.boom:
            raise ConnectionError("db mati")
        self.profile.update(patch)
        self.profile_writes.append(dict(patch))
        return dict(self.profile)

    async def put_fact(self, text):
        if self.boom:
            raise ConnectionError("db mati")
        self.facts.append(text)
        return "fact:test"


def _chunk(text):
    return {"event": "on_chat_model_stream", "name": "m",
            "data": {"chunk": AIMessage(content=text)}}


def _agent(graph, memory):
    class Chat(BaseChatAgent):
        AGENT_NAME = "t"
        SYSTEM_PROMPT = "x"
        TOOLS = []

    ag = Chat.__new__(Chat)
    ag.runner = Runner()
    ag.graph = graph
    ag.memory = memory
    return ag


def _run(coro):
    return asyncio.run(coro)


async def _collect(ag, text):
    return [e async for e in
            ag.stream_agent(text, {"configurable": {"thread_id": "t"}})]


def test_profile_injected_as_system_message():
    graph = FakeGraph([_chunk("ok")], [AIMessage(content="ok")])
    ag = _agent(graph, FakeMemory({"nama": "Budi", "diet": "vegetarian"}))
    events = _run(_collect(ag, "halo"))
    assert events[-1]["type"] == "result"
    first = graph.seen_states[0]["messages"][0]
    assert isinstance(first, SystemMessage)
    assert "Budi" in first.content and "vegetarian" in first.content
    assert isinstance(graph.seen_states[0]["messages"][1], HumanMessage)


def test_no_memory_no_system_message():
    graph = FakeGraph([_chunk("ok")], [AIMessage(content="ok")])
    ag = _agent(graph, None)
    events = _run(_collect(ag, "halo"))
    assert events[-1]["type"] == "result"
    assert all(not isinstance(m, SystemMessage)
               for m in graph.seen_states[0]["messages"])


def test_memory_boom_stream_keeps_working():
    """Store mati → chat tetap result (fail-soft), tanpa SystemMessage profil."""
    graph = FakeGraph([_chunk("ok")], [AIMessage(content="ok")])
    ag = _agent(graph, FakeMemory(boom=True))
    events = _run(_collect(ag, "ingat ya: saya vegetarian"))
    kinds = [e["type"] for e in events]
    assert kinds == ["started", "token", "result"]


def test_heuristic_fact_persisted_on_success():
    graph = FakeGraph([_chunk("siap")], [AIMessage(content="siap")])
    mem = FakeMemory()
    events = _run(_collect(_agent(graph, mem), "ingat ya: saya vegetarian"))
    assert events[-1]["type"] == "result"
    assert mem.facts == ["saya vegetarian"]


def test_heuristic_name_goes_to_profile():
    graph = FakeGraph([_chunk("hai")], [AIMessage(content="hai")])
    mem = FakeMemory()
    _run(_collect(_agent(graph, mem), "nama saya Budi"))
    assert mem.profile_writes == [{"nama": "Budi"}]
    assert mem.facts == []


def test_heuristic_skipped_on_failed_turn():
    graph = FakeGraph([_chunk("a"), RuntimeError("boom")], [])
    mem = FakeMemory()
    events = _run(_collect(_agent(graph, mem), "ingat ya: saya vegetarian"))
    assert events[-1]["type"] == "error"
    assert mem.facts == [] and mem.profile_writes == []


def test_facts_wired_into_prompt():
    graph = FakeGraph([_chunk("ok")], [AIMessage(content="ok")])
    mem = FakeMemory({"nama": "Budi"}, fact_hits=["punya toko di Bandung"])
    events = _run(_collect(_agent(graph, mem), "rekomendasikan makan siang"))
    assert events[-1]["type"] == "result"
    first = graph.seen_states[0]["messages"][0]
    assert isinstance(first, SystemMessage)
    assert "Budi" in first.content and "toko di Bandung" in first.content


def test_profile_id_stable_replaces_across_turns():
    """ID stabil → add_messages REPLACE profil lama (tak menumpuk)."""
    from langgraph.graph.message import add_messages

    from app.infra.memory import PROFILE_MSG_ID

    turn1 = add_messages(
        [],
        [SystemMessage(content="p1", id=PROFILE_MSG_ID), HumanMessage(content="h1")],
    )
    turn2 = add_messages(
        turn1,
        [SystemMessage(content="p2", id=PROFILE_MSG_ID), HumanMessage(content="h2")],
    )
    profiles = [m for m in turn2 if getattr(m, "id", None) == PROFILE_MSG_ID]
    assert len(profiles) == 1 and profiles[0].content == "p2"
    assert len(turn2) == 3  # 1 profil + 2 user (bukan 4)
