"""ThreadController: state/history/fork/delete (tanpa DB — fake checkpointer)."""

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agents.ExampleChatAgent import ExampleChatAgent
from app.controllers.ThreadController import ThreadController, preview_message
from app.controllers.registry import CHAT_AGENTS
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import (
    AgentNotFoundError,
    CrossTenantError,
    ThreadNotFoundError,
)
from app.core.threads import ThreadIdentity
from app.infra.memory import PROFILE_MSG_ID


class FakeTuple:
    def __init__(self, thread_id, checkpoint_id, values, step, source, ts):
        self.config = {"configurable": {
            "thread_id": thread_id, "checkpoint_id": checkpoint_id}}
        self.checkpoint = {"ts": ts, "channel_values": dict(values)}
        self.metadata = {"step": step, "source": source}


class FakeCheckpointer:
    """Meniru subset AsyncPostgresSaver: aget_tuple/alist/adelete_thread."""

    def __init__(self, threads=None):
        self.threads: dict = threads or {}

    async def aget_tuple(self, config):
        c = config.get("configurable", {})
        items = self.threads.get(c.get("thread_id"), [])
        if c.get("checkpoint_id"):
            return next((t for t in items
                         if t.config["configurable"]["checkpoint_id"] == c["checkpoint_id"]),
                        None)
        return items[-1] if items else None

    async def alist(self, config):
        for t in self.threads.get(config.get("configurable", {}).get("thread_id"), []):
            yield t

    async def adelete_thread(self, thread_id):
        self.threads.pop(thread_id, None)


def _settings():
    return Settings(_env_file=None)


def _ident():
    return ThreadIdentity(tenant="toko-a", user="budi")


def _msgs(n=2):
    out = []
    for i in range(n):
        out.append(HumanMessage(content=f"q{i}"))
        out.append(AIMessage(content=f"a{i}"))
    return out


def _cp():
    t1 = "toko-a:budi:sess-1"
    return FakeCheckpointer({t1: [
        FakeTuple(t1, "cp-1", {"messages": _msgs(1)}, 0, "input", "2026-01-01T00:00:00"),
        FakeTuple(t1, "cp-2", {"messages": _msgs(2)}, 2, "loop", "2026-01-01T00:01:00"),
        FakeTuple(t1, "cp-3", {"messages": _msgs(3)}, 4, "loop", "2026-01-01T00:02:00"),
    ]})


def _cp_with_profile():
    """Checkpoint berisi pesan profil infra (wajib disembunyikan)."""
    t1 = "toko-a:budi:sess-1"
    msgs = [SystemMessage(content="Profil user tersimpan: nama: Budi",
                          id=PROFILE_MSG_ID),
            HumanMessage(content="q0"), AIMessage(content="a0")]
    return FakeCheckpointer({t1: [
        FakeTuple(t1, "cp-1", {"messages": msgs}, 0, "input", "2026-01-01T00:00:00"),
    ]})


def test_preview_roles_and_truncation():
    assert preview_message(HumanMessage(content="hi")).role == "user"
    assert preview_message(AIMessage(content="ho")).role == "assistant"
    tool_msg = ToolMessage(content="42", tool_call_id="c1", name="check_stock")
    pv = preview_message(tool_msg)
    assert (pv.role, pv.tool) == ("tool", "check_stock")
    long_pv = preview_message(HumanMessage(content="x" * 3000))
    assert long_pv.truncated and len(long_pv.content) == 2000
    dict_pv = preview_message({"role": "user", "content": "yo"})
    assert (dict_pv.role, dict_pv.content) == ("user", "yo")
    assert preview_message({"type": "human", "content": "h"}).role == "user"
    assert preview_message({"type": "ai", "content": "a"}).role == "assistant"
    assert preview_message({"type": "alien", "content": "?"}).role == "unknown"


@pytest.mark.asyncio
async def test_get_state_ok():
    ctrl = ThreadController(_settings(), _cp(), Runner())
    res = await ctrl.get_state(_ident(), "toko-a:budi:sess-1")
    assert res.checkpoint_id == "cp-3"  # terbaru
    assert res.messages_count == 6
    assert res.step == 4 and res.source == "loop"
    assert res.created_at == "2026-01-01T00:02:00"
    assert [m.role for m in res.messages[:2]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_get_state_404_unknown():
    ctrl = ThreadController(_settings(), _cp(), Runner())
    with pytest.raises(ThreadNotFoundError) as ei:
        await ctrl.get_state(_ident(), "toko-a:budi:sess-9")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_owner_checked_before_existence():
    """Thread tenant lain → 403 (bukan 404): jangan bocorkan keberadaan."""
    ctrl = ThreadController(_settings(), _cp(), Runner())
    other = ThreadIdentity(tenant="toko-b", user="budi")
    with pytest.raises(CrossTenantError):
        await ctrl.get_state(other, "toko-a:budi:sess-1")
    with pytest.raises(CrossTenantError):
        await ctrl.delete_thread(other, "toko-a:budi:sess-1")


@pytest.mark.asyncio
async def test_history_order_and_pagination():
    ctrl = ThreadController(_settings(), _cp(), Runner())
    full = await ctrl.get_history(_ident(), "toko-a:budi:sess-1", limit=10)
    assert full.total == 3
    assert [i.checkpoint_id for i in full.items] == ["cp-3", "cp-2", "cp-1"]
    assert full.items[0].messages_count == 6
    page = await ctrl.get_history(_ident(), "toko-a:budi:sess-1", limit=1, offset=1)
    assert [i.checkpoint_id for i in page.items] == ["cp-2"]
    assert (page.limit, page.offset, page.total) == (1, 1, 3)
    with pytest.raises(ThreadNotFoundError):
        await ctrl.get_history(_ident(), "toko-a:budi:sess-9")


@pytest.mark.asyncio
async def test_delete_then_gone():
    cp = _cp()
    ctrl = ThreadController(_settings(), cp, Runner())
    await ctrl.delete_thread(_ident(), "toko-a:budi:sess-1")
    with pytest.raises(ThreadNotFoundError):
        await ctrl.get_state(_ident(), "toko-a:budi:sess-1")
    with pytest.raises(ThreadNotFoundError):  # hapus 2x → 404, bukan 500
        await ctrl.delete_thread(_ident(), "toko-a:budi:sess-1")


@pytest.mark.asyncio
async def test_fork_validations():
    ctrl = ThreadController(_settings(), _cp(), Runner())
    with pytest.raises(ThreadNotFoundError):
        await ctrl.fork(_ident(), "toko-a:budi:sess-9", "cp-1", "example_chat")
    with pytest.raises(ThreadNotFoundError):
        await ctrl.fork(_ident(), "toko-a:budi:sess-1", "cp-salah", "example_chat")
    with pytest.raises(AgentNotFoundError):
        await ctrl.fork(_ident(), "toko-a:budi:sess-1", "cp-1", "tak-ada")
    with pytest.raises(CrossTenantError):
        other = ThreadIdentity(tenant="toko-b", user="budi")
        await ctrl.fork(other, "toko-a:budi:sess-1", "cp-1", "example_chat")


@pytest.mark.asyncio
async def test_fork_success_writes_new_thread(monkeypatch):
    """Fork sukses: values checkpoint sumber disalin ke thread baru (agen fake)."""
    written = {}

    class FakeGraph:
        async def aupdate_state(self, config, values):
            written["config"] = config
            written["values"] = values

    class FakeAgent:
        def __init__(self, ctx):
            self.graph = FakeGraph()

    monkeypatch.setitem(CHAT_AGENTS, "fake_chat", FakeAgent)
    cp = _cp()
    ctrl = ThreadController(_settings(), cp, Runner())
    res = await ctrl.fork(_ident(), "toko-a:budi:sess-1", "cp-2", "fake_chat")
    assert res.thread_id.startswith("toko-a:budi:sess-")
    assert res.thread_id != "toko-a:budi:sess-1"
    assert res.forked_from == "cp-2" and res.agent == "fake_chat"
    assert written["config"] == {"configurable": {"thread_id": res.thread_id}}
    assert len(written["values"]["messages"]) == 4  # values milik cp-2
    # salinan DALAM: mutasi hasil fork tak bocor ke snapshot sumber
    written["values"]["messages"].append(HumanMessage(content="x"))
    src_msgs = cp.threads["toko-a:budi:sess-1"][1].checkpoint["channel_values"]["messages"]
    assert len(src_msgs) == 4


@pytest.mark.asyncio
async def test_fork_rejects_blank_checkpoint_id():
    """cid kosong/spasi → 400 eksplisit (input invalid, bukan thread hilang)."""
    from app.core.errors import InvalidThreadIdError
    ctrl = ThreadController(_settings(), _cp(), Runner())
    with pytest.raises(InvalidThreadIdError):
        await ctrl.fork(_ident(), "toko-a:budi:sess-1", "   ", "example_chat")


def test_graph_for_fork_tolerates_empty_llm_creds():
    """Fork operasi DB murni: build graph tak boleh 502 tanpa LITELLM_*."""
    from langgraph.checkpoint.memory import InMemorySaver

    ctrl = ThreadController(Settings(_env_file=None), InMemorySaver(), Runner())
    graph = ctrl._graph_for_fork(ExampleChatAgent, "toko-a:budi:sess-x")
    assert graph is not None


@pytest.mark.asyncio
async def test_profile_message_hidden_from_state_and_history():
    ctrl = ThreadController(_settings(), _cp_with_profile(), Runner())
    state = await ctrl.get_state(_ident(), "toko-a:budi:sess-1")
    assert state.messages_count == 2  # profil tak dihitung
    assert all("Profil user tersimpan" not in m.content for m in state.messages)
    assert [m.role for m in state.messages] == ["user", "assistant"]
    hist = await ctrl.get_history(_ident(), "toko-a:budi:sess-1")
    assert hist.items[0].messages_count == 2


@pytest.mark.asyncio
async def test_get_state_last_n():
    ctrl = ThreadController(_settings(), _cp(), Runner())
    full = await ctrl.get_state(_ident(), "toko-a:budi:sess-1")
    assert full.messages_count == 6
    tail = await ctrl.get_state(_ident(), "toko-a:budi:sess-1", last_n=2)
    assert tail.messages_count == 2
    assert [m.content for m in tail.messages] == ["q2", "a2"]
