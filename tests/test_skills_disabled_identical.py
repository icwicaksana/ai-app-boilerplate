"""v0.9 F2 gate: SKILLS_ENABLED=false → prompt byte-identik v0.8.3.

Setiap agen chat dibangun dengan skills MATI, lalu system prompt yang
diteruskan ke `create_agent` di-hash. Hash di bawah ini adalah BASELINE yang
diambil saat F2 — sebelum F3 menyentuh `build()`.

Kalau tes ini merah setelah perubahan F3/F4, artinya jalur lama ikut berubah:
perbaiki wiring-nya, JANGAN ganti hash-nya. Hash hanya boleh berubah bila ada
keputusan eksplisit mengubah prompt v0.8.3 (dan itu di luar cakupan v0.9).
"""

from __future__ import annotations

import hashlib

import pytest

from app.agents.factory import build_agent_context
from app.controllers.registry import CHAT_AGENTS
from app.core.config import Settings

# sha256[:16] atas system prompt lengkap per agen (SKILLS_ENABLED=false).
# Dibangkitkan oleh Test/spike_v09/probe_prompt_hash.py pada 2026-11-17.
BASELINE_PROMPT_SHA = {
    "example_chat": "735e61df6882a61c",
    "rag_chat": "397a3af91b5c8c47",
}
BASELINE_PROMPT_SHA_MEMORY = {
    "example_chat": "d5d2aaeb29ded56b",
    "rag_chat": "0a450de73732cc22",
}

HITL_DEMO_PROMPT = "Demo HITL tanpa LLM."


def _settings() -> Settings:
    return Settings(
        _env_file=None, JWT_SECRET="x" * 40, JWT_ISSUER="ai-app-boilerplate",
        LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini",
        SKILLS_ENABLED=False,
    )


def _build_capture(agent_name: str, *, with_memory: bool, monkeypatch):
    """Bangun agen sambil menangkap kwargs yang diteruskan ke create_agent."""
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
        settings=_settings(), checkpointer=None, runner=None,
        session_id="toko-a:budi:sess-1",
        store=object() if with_memory else None,
        agent_cls=cls, missing_retriever="degrade",
    )
    agent = cls(ctx)
    return agent, captured


def _sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


@pytest.mark.parametrize("agent_name", sorted(BASELINE_PROMPT_SHA))
def test_prompt_tanpa_skill_identik_baseline(agent_name, monkeypatch):
    """Tanpa memori: prompt harus sama byte-per-byte dengan v0.8.3."""
    _, captured = _build_capture(agent_name, with_memory=False,
                                 monkeypatch=monkeypatch)
    prompt = captured["system_prompt"]
    assert _sha(prompt) == BASELINE_PROMPT_SHA[agent_name]
    assert "load_skill" not in prompt
    assert "read_skill_file" not in prompt


@pytest.mark.parametrize("agent_name", sorted(BASELINE_PROMPT_SHA_MEMORY))
def test_prompt_dengan_memori_identik_baseline(agent_name, monkeypatch):
    """Dengan memori: MEMORY_RULES menempel persis seperti v0.8.3."""
    _, captured = _build_capture(agent_name, with_memory=True,
                                 monkeypatch=monkeypatch)
    prompt = captured["system_prompt"]
    assert _sha(prompt) == BASELINE_PROMPT_SHA_MEMORY[agent_name]
    assert "load_skill" not in prompt


@pytest.mark.parametrize("agent_name", sorted(BASELINE_PROMPT_SHA))
def test_tidak_ada_tool_skill_terikat(agent_name, monkeypatch):
    _, captured = _build_capture(agent_name, with_memory=False,
                                 monkeypatch=monkeypatch)
    names = [getattr(t, "name", "?") for t in captured["tools"]]
    assert "load_skill" not in names
    assert "read_skill_file" not in names


def test_hitl_demo_prompt_terkunci():
    """hitl_demo tidak lewat build() induk — kuncinya literal, bukan hash."""
    assert CHAT_AGENTS["hitl_demo"].SYSTEM_PROMPT == HITL_DEMO_PROMPT


def test_semua_agen_tercakup_baseline():
    """Agen chat baru yang lupa didaftarkan di sini = baseline tak lengkap."""
    covered = set(BASELINE_PROMPT_SHA) | {"hitl_demo"}
    assert set(CHAT_AGENTS) == covered
