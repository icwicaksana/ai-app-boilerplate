"""v0.9 F4: mount delegasi di build() — guard ganda + indeks di akhir."""

from __future__ import annotations

import pytest

from app.agents.factory import build_agent_context
from app.agents.subagents.registry import SUBAGENTS, SubagentSpec
from app.agents.tools.DelegationTool import SubagentConfigError
from app.controllers.registry import CHAT_AGENTS
from app.core.config import Settings


def _settings(**kw) -> Settings:
    base = dict(_env_file=None, JWT_SECRET="x" * 40,
                JWT_ISSUER="ai-app-boilerplate",
                LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini")
    base.update(kw)
    return Settings(**base)


def _spec(**kw) -> SubagentSpec:
    base = dict(name="cek", description="cek stok",
                when_to_use="user tanya stok",
                system_prompt="Kamu pengecek stok.", tools=())
    base.update(kw)
    return SubagentSpec(**base)


def _build_capture(agent_name: str, *, settings, monkeypatch):
    import app.agents.BaseChatAgent as B

    captured: dict = {}

    def fake_create_agent(**kw):
        captured.update(kw)

        class _Graph:
            name = kw.get("name", "LangGraph")

        return _Graph()

    # Hanya create_agent INDUK yang dipalsukan (untuk capture kwargs).
    # Child dibangun betulan (tanpa jaringan — konstruktor saja).
    monkeypatch.setattr(B, "create_agent", fake_create_agent)
    cls = CHAT_AGENTS[agent_name]
    ctx = build_agent_context(
        settings=settings, checkpointer=None, runner=None,
        session_id="toko-a:budi:sess-1",
        agent_cls=cls, missing_retriever="degrade")
    agent = cls(ctx)
    return agent, captured


def test_delegasi_terikat_dan_indeks_di_akhir(monkeypatch):
    monkeypatch.setitem(SUBAGENTS, "cek", _spec())
    agent, captured = _build_capture(
        "example_chat", settings=_settings(SUBAGENTS_ENABLED=True),
        monkeypatch=monkeypatch)
    assert "subagent__cek" in agent.bound_tools
    prompt = captured["system_prompt"]
    # Roster template berisi demo-stok + spec tes; indeks terurut alfabetis.
    assert "- cek:" in prompt and "- demo-stok:" in prompt
    assert prompt.index("- cek:") < prompt.index("- demo-stok:")
    last = prompt.rstrip().rsplit("\n", 1)[-1]
    assert last.startswith("- demo-stok:"), last
    assert "subagent__<nama>" in prompt


def test_flag_mati_roster_ada_tak_tersentuh(monkeypatch):
    """Guard ganda sisi flag: roster penuh tapi flag mati → jalur mati."""
    monkeypatch.setitem(SUBAGENTS, "cek", _spec())
    agent, captured = _build_capture(
        "example_chat", settings=_settings(SUBAGENTS_ENABLED=False),
        monkeypatch=monkeypatch)
    assert "subagent__cek" not in agent.bound_tools
    assert "Subagent yang tersedia" not in captured["system_prompt"]


def test_roster_kosong_flag_nyala_tak_tersentuh(monkeypatch):
    """Guard ganda sisi roster: flag nyala tapi roster kosong → jalur mati."""
    import app.agents.subagents.registry as _reg

    # Roster template berisi demo-stok; kosongkan untuk skenario ini.
    # (build() import SUBAGENTS di dalam fungsi → patch berlaku.)
    monkeypatch.setattr(_reg, "SUBAGENTS", {})
    agent, captured = _build_capture(
        "example_chat", settings=_settings(SUBAGENTS_ENABLED=True),
        monkeypatch=monkeypatch)
    assert "subagent__cek" not in agent.bound_tools
    assert "Subagent yang tersedia" not in captured["system_prompt"]


def test_spec_rusak_melempar_di_build_bukan_diam(monkeypatch):
    """Salah wiring harus ketahuan di test/deploy, bukan skip diam-diam."""
    monkeypatch.setitem(SUBAGENTS, "rusak",
                        _spec(name="rusak", tools=("tak-ada",)))
    with pytest.raises(SubagentConfigError, match="tak tersedia"):
        _build_capture("example_chat",
                       settings=_settings(SUBAGENTS_ENABLED=True),
                       monkeypatch=monkeypatch)
