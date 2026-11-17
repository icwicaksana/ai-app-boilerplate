"""v0.9 F3.3: wiring registry → build() — jalur AKTIF.

Kebalikan dari tests/test_skills_disabled_identical.py (jalur mati, hash
terkunci): di sini SKILLS_ENABLED=true dengan registry nyata, dan yang
dibuktikan adalah (a) tool terikat, (b) indeks di AKHIR prompt (K5),
(c) prefix prompt sebelum indeks byte-identik baseline v0.8.3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.factory import build_agent_context
from app.controllers.registry import CHAT_AGENTS
from app.core.config import Settings
from app.skills.loader import load_skills
from app.skills.registry import SkillRegistry

GOOD = Path(__file__).parent / "fixtures" / "skills_boot" / "good"


def _settings(**kw) -> Settings:
    base = dict(_env_file=None, JWT_SECRET="x" * 40,
                JWT_ISSUER="ai-app-boilerplate",
                LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini",
                SKILLS_ENABLED=True)
    base.update(kw)
    return Settings(**base)


def _registry() -> SkillRegistry:
    skills, errors = load_skills(GOOD)
    assert not errors
    return SkillRegistry.build(skills, root=str(GOOD))


def _settings_off(**kw) -> Settings:
    base = dict(_env_file=None, JWT_SECRET="x" * 40,
                JWT_ISSUER="ai-app-boilerplate",
                LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini",
                SKILLS_ENABLED=False)
    base.update(kw)
    return Settings(**base)


def _prompt_off(agent_name: str, monkeypatch) -> str:
    """Prompt jalur mati — pembanding prefix (hash-nya dikunci berkas gate)."""
    _, captured = _build_capture(agent_name, registry=None,
                                 settings=_settings_off(),
                                 monkeypatch=monkeypatch)
    return captured["system_prompt"]


_AGENTS = ["example_chat", "rag_chat"]


def _build_capture(agent_name: str, *, registry, settings, monkeypatch,
                   with_memory: bool = False):
    import app.agents.BaseChatAgent as B

    captured: dict = {}

    def fake_create_agent(**kw):
        captured.update(kw)

        class _Graph:
            name = kw.get("name", "LangGraph")

        return _Graph()

    monkeypatch.setattr(B, "create_agent", fake_create_agent)
    cls = CHAT_AGENTS[agent_name]
    ctx = build_agent_context(
        settings=settings, checkpointer=None, runner=None,
        session_id="toko-a:budi:sess-1",
        store=object() if with_memory else None,
        skills=registry, agent_cls=cls, missing_retriever="degrade",
    )
    agent = cls(ctx)
    return agent, captured


def test_tool_skill_terikat_saat_aktif(monkeypatch):
    agent, captured = _build_capture("example_chat", registry=_registry(),
                                     settings=_settings(), monkeypatch=monkeypatch)
    assert "load_skill" in agent.bound_tools
    assert "read_skill_file" in agent.bound_tools
    assert [t.name for t in captured["tools"] if
            t.name in ("load_skill", "read_skill_file")] == [
               "load_skill", "read_skill_file"]


def test_indeks_di_akhir_prompt(monkeypatch):
    _, captured = _build_capture("example_chat", registry=_registry(),
                                 settings=_settings(), monkeypatch=monkeypatch)
    prompt = captured["system_prompt"]
    last_line = prompt.rstrip().rsplit("\n", 1)[-1]
    assert last_line.startswith("- zebra:"), last_line
    head, sep, tail = prompt.partition("Skill yang tersedia")
    assert sep, "blok indeks tidak ditemukan di prompt"
    assert "- alpha:" in tail and "- zebra:" in tail


def test_prefix_sebelum_indeks_identik_baseline(monkeypatch):
    """Prefix-cache: bagian depan prompt tak berubah satu byte pun."""
    for agent_name in _AGENTS:
        _, cap_on = _build_capture(agent_name, registry=_registry(),
                                   settings=_settings(), monkeypatch=monkeypatch)
        baseline_prompt = _prompt_off(agent_name, monkeypatch)
        on_prompt = cap_on["system_prompt"]
        assert on_prompt.startswith(baseline_prompt), agent_name
        assert on_prompt[len(baseline_prompt):].startswith(
            "\n\nSkill yang tersedia")


def test_registry_kosong_identik_mati(monkeypatch):
    """Guard ganda: enabled TAPI registry kosong → perilaku jalur mati."""
    for agent_name in _AGENTS:
        _, captured = _build_capture(agent_name, registry=SkillRegistry.empty(),
                                     settings=_settings(), monkeypatch=monkeypatch)
        assert captured["system_prompt"] == _prompt_off(agent_name, monkeypatch)


def test_registry_none_identik_mati(monkeypatch):
    """Registry None (lifespan lama) → perilaku jalur mati, bukan crash."""
    for agent_name in _AGENTS:
        _, captured = _build_capture(agent_name, registry=None,
                                     settings=_settings(), monkeypatch=monkeypatch)
        assert captured["system_prompt"] == _prompt_off(agent_name, monkeypatch)


def test_urutan_prompt_memory_lalu_skill(monkeypatch):
    """K5 + blok memori: SYSTEM → MEMORY_RULES → SKILLS_INDEX."""
    _, captured = _build_capture("example_chat", registry=_registry(),
                                 settings=_settings(), monkeypatch=monkeypatch,
                                 with_memory=True)
    prompt = captured["system_prompt"]
    i_mem = prompt.index("Aturan memori user")
    i_skill = prompt.index("Skill yang tersedia")
    assert 0 < i_mem < i_skill


def test_factory_meneruskan_registry(monkeypatch):
    reg = _registry()
    ctx = build_agent_context(
        settings=_settings(), checkpointer=None, runner=None,
        session_id="toko-a:budi:sess-1", skills=reg)
    assert ctx.skills is reg


@pytest.mark.asyncio
async def test_bound_skill_tool_bekerja_ujung_ke_ujung(monkeypatch):
    """Tool terikat dari build() benar-benar membaca registry fixture."""
    _, captured = _build_capture("example_chat", registry=_registry(),
                                 settings=_settings(), monkeypatch=monkeypatch)
    tools = {t.name: t for t in captured["tools"]}
    out = await tools["load_skill"].ainvoke({"name": "alpha"})
    assert "Body skill alpha" in out
    assert "references/detail.md" in out
    out2 = await tools["read_skill_file"].ainvoke(
        {"name": "alpha", "path": "references/detail.md"})
    assert "Detail Alpha" in out2
