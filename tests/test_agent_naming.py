"""v0.9 F0-3: setiap graph agent punya NAMA.

Tanpa `name=`, semua graph bernama "LangGraph" sehingga StreamPolicy tidak
bisa membedakan run milik siapa saat subagent ditambahkan, dan log/trace
tidak bisa diatribusikan ke agen tertentu.
"""

from __future__ import annotations

import pytest

from app.controllers.registry import CHAT_AGENTS


@pytest.mark.parametrize("agent_name", sorted(CHAT_AGENTS))
def test_graph_punya_nama_agen(agent_name, monkeypatch):
    """Graph yang dibangun `build()` harus membawa AGENT_NAME."""
    from app.agents.factory import build_agent_context
    from app.core.config import Settings

    agent_cls = CHAT_AGENTS[agent_name]
    settings = Settings(_env_file=None, JWT_SECRET="x" * 40,
                        JWT_ISSUER="ai-app-boilerplate",
                        LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini")
    ctx = build_agent_context(
        settings=settings, checkpointer=None, runner=None,
        session_id="toko-a:budi:sess-1", agent_cls=agent_cls,
        missing_retriever="degrade",
    )
    agent = agent_cls(ctx)
    assert agent.graph.name == agent.AGENT_NAME


def test_nama_agen_unik():
    """Dua agen bernama sama = filter stream ambigu."""
    nama = [getattr(c, "AGENT_NAME", None) for c in CHAT_AGENTS.values()]
    assert all(nama), "setiap agen wajib punya AGENT_NAME"
    assert len(nama) == len(set(nama)), f"nama duplikat: {nama}"
