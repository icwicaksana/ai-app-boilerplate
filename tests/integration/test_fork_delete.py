"""Fork + delete di DB nyata (graph create_agent asli, TANPA invoke LLM).

Seed memakai `aupdate_state` (tulis checkpoint murni, tanpa panggil model),
sehingga kredensial LLM dummy cukup dan tanpa akses jaringan.
"""

import pytest
from langchain_core.messages import HumanMessage

from app.agents.AgentContext import AgentContext
from app.agents.ExampleChatAgent import ExampleChatAgent
from app.controllers.ThreadController import ThreadController
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import ThreadNotFoundError
from app.core.threads import ThreadIdentity
from app.infra.memory import MemoryStore

pytestmark = pytest.mark.integration


def _dummy_settings():
    return Settings(_env_file=None, LITELLM_BASE_URL="http://localhost:4000",
                    LITELLM_API_KEY="dummy-tidak-dipakai", LITELLM_MODEL="dummy")


@pytest.mark.asyncio
async def test_fork_copies_prefix_then_delete_source(persistence, tag):
    _, checkpointer, store = persistence
    workspace, user = f"it-{tag}", "budi"
    ident = ThreadIdentity(workspace=workspace, user=user)
    src = f"{workspace}:{user}:sess-src"

    ctx = AgentContext(settings=_dummy_settings(), checkpointer=checkpointer,
                       runner=Runner(), session_id=src)
    graph = ExampleChatAgent(ctx).graph  # build only — tanpa jaringan

    # seed 2 checkpoint: [m0] lalu [m0, m1]
    await graph.aupdate_state({"configurable": {"thread_id": src}},
                              {"messages": [HumanMessage(content="m0")]})
    await graph.aupdate_state({"configurable": {"thread_id": src}},
                              {"messages": [HumanMessage(content="m0"),
                                            HumanMessage(content="m1")]})

    ctrl = ThreadController(_dummy_settings(), checkpointer, Runner(),
                             store=store)
    mem = MemoryStore(store, workspace, user)
    await mem.put_profile({"nama": "Budi"})
    forked_id: str | None = None
    try:
        hist = await ctrl.get_history(ident, src, limit=10)
        assert hist.total == 2
        old_cp = hist.items[-1].checkpoint_id  # checkpoint pertama ([m0])

        forked = await ctrl.fork(ident, src, old_cp, "example_chat")
        forked_id = forked.thread_id
        assert forked.thread_id.startswith(f"{workspace}:{user}:sess-")
        assert forked.thread_id != src

        state = await ctrl.get_state(ident, forked.thread_id)
        assert state.messages_count == 1  # prefix s/d checkpoint lama
        assert state.messages[0].content == "m0"

        # delete sumber → 404; fork + profil tetap utuh
        await ctrl.delete_thread(ident, src)
        with pytest.raises(ThreadNotFoundError):
            await ctrl.get_state(ident, src)
        still = await ctrl.get_state(ident, forked.thread_id)
        assert still.messages_count == 1
        assert (await mem.get_profile())["nama"] == "Budi"
    finally:
        for tid in [src] + ([forked_id] if forked_id else []):
            try:
                await checkpointer.adelete_thread(tid)
            except Exception:
                pass
