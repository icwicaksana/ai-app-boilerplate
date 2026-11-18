"""WS2: blok prompt NARASI always-on tanpa flag.

Keputusan TERKUNCI user: ALWAYS-ON TANPA FLAG (abaikan usulan conditional).
Blok `NARRATION_RULES` ditempel SETELAH SYSTEM_PROMPT dan SEBELUM MEMORY_RULES;
indeks skill (K5) + delegasi tetap di AKHIR prompt agar prefix-cache stabil.

Yang dikunci di sini:
  (a) 9 kata kunci redaksional + panjang ~550 char + tanpa few-shot,
  (b) always-on di 8 varian flag (skill x subagen x memori),
  (c) urutan blok SYSTEM -> NARASI -> MEMORI -> SKILL -> DELEGASI,
  (d) prefix byte struktural (SYSTEM + NARASI stabil, indeks tetap akhir),
  (e) hitl_demo tak kena (override build sendiri),
  (f) bound_tools tak berubah oleh narasi (narasi = prompt saja).
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from app.agents.BaseChatAgent import MEMORY_RULES, NARRATION_RULES
from app.agents.factory import build_agent_context
from app.controllers.registry import CHAT_AGENTS
from app.core.config import Settings
from app.skills.loader import load_skills
from app.skills.registry import SkillRegistry

# 9 kata kunci WS2 — cengkeraman redaksional (case-sensitive sesuai blok).
EXPECTED_KEYWORDS = (
    "narasi",
    "suara",
    "Indonesia",
    "kalimat",
    "SEBELUM",
    "SESUDAH",
    "subagent__",
    "micro-tool",
    "thinking",
)

GOOD = Path(__file__).parent / "fixtures" / "skills_boot" / "good"


def _settings(**kw) -> Settings:
    base = dict(
        _env_file=None, JWT_SECRET="x" * 40,
        JWT_ISSUER="ai-app-boilerplate",
        LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini",
        SKILLS_ENABLED=False, SUBAGENTS_ENABLED=False,
    )
    base.update(kw)
    return Settings(**base)


def _registry() -> SkillRegistry:
    skills, errors = load_skills(GOOD)
    assert not errors
    return SkillRegistry.build(skills, root=str(GOOD))


def _build_capture(agent_name: str, *, settings, monkeypatch,
                   with_memory: bool = False, registry=None):
    """Bangun agen sambil menangkap kwargs ke create_agent (metode gate)."""
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


# --- (a) 9 kata + panjang + tanpa few-shot ---------------------------------


def test_narasi_kunci_9_kata():
    """Blok narasi menyebut 9 kata kunci WS2 (gagal bila redaksi drift)."""
    for keyword in EXPECTED_KEYWORDS:
        assert keyword in NARRATION_RULES, \
            f"NARRATION_RULES kehilangan kata: {keyword}"


def test_narasi_panjang_sekitar_550_char_tanpa_fewshot():
    """~550 char, 1 paragraf, tanpa contoh few-shot (hemat token)."""
    n = len(NARRATION_RULES)
    assert 500 <= n <= 600, f"panjang NARRATION_RULES={n}, harus ~550"
    assert "\n" not in NARRATION_RULES, "narasi harus 1 paragraf (tanpa newline)"
    lowered = NARRATION_RULES.lower()
    for marker in ("<example", "contoh:", "input:", "output:",
                   "misal:", "few-shot", "fewshot"):
        assert marker not in lowered, f"narasi mengandung few-shot: {marker}"


# --- (b) always-on di 8 varian flag ----------------------------------------

_FLAG_COMBOS = list(itertools.product([False, True], repeat=3))
# (skills_enabled, subagents_enabled, with_memory)


@pytest.mark.parametrize(
    "skills_on,subagents_on,with_memory", _FLAG_COMBOS,
    ids=[f"skill{int(s)}-sub{int(d)}-mem{int(m)}"
         for s, d, m in _FLAG_COMBOS],
)
def test_narasi_always_on_8_varian_flag(
        skills_on, subagents_on, with_memory, monkeypatch):
    """Narasi hadir di SEMUA kombinasi flag — tanpa pengecualian.

    Roster template (demo-stok, tool check_stock) kompatibel dengan
    example_chat, sehingga 8 varian bisa dibangun tanpa patch roster.
    """
    settings = _settings(SKILLS_ENABLED=skills_on,
                         SUBAGENTS_ENABLED=subagents_on)
    registry = _registry() if skills_on else None
    _, captured = _build_capture(
        "example_chat", settings=settings, monkeypatch=monkeypatch,
        with_memory=with_memory, registry=registry)
    prompt = captured["system_prompt"]
    assert NARRATION_RULES in prompt
    assert "Aturan narasi" in prompt


def test_narasi_always_on_rag_chat_4_varian_tanpa_delegasi(monkeypatch):
    """rag_chat (degrade, tanpa check_stock) tak bisa mount demo-stok — jadi
    4 varian skill x memori dengan SUBAGENTS_ENABLED=false tetap wajib narasi.

    Varian delegasi-nyala untuk rag_chat diuji terpisah dengan roster kosong
    (guard roster) agar tak ConfigError.
    """
    for skills_on, with_memory in itertools.product([False, True], repeat=2):
        settings = _settings(SKILLS_ENABLED=skills_on,
                             SUBAGENTS_ENABLED=False)
        registry = _registry() if skills_on else None
        _, captured = _build_capture(
            "rag_chat", settings=settings, monkeypatch=monkeypatch,
            with_memory=with_memory, registry=registry)
        assert NARRATION_RULES in captured["system_prompt"], \
            f"rag_chat skill={skills_on} mem={with_memory} kehilangan narasi"


def test_narasi_tetap_hadir_rag_subagen_nyala_roster_kosong(monkeypatch):
    """Flag SUBAGENTS nyala + roster kosong = delegasi mati, narasi tetap."""
    import app.agents.subagents.registry as _reg

    monkeypatch.setattr(_reg, "SUBAGENTS", {})
    settings = _settings(SUBAGENTS_ENABLED=True)
    _, captured = _build_capture("rag_chat", settings=settings,
                                 monkeypatch=monkeypatch)
    prompt = captured["system_prompt"]
    assert NARRATION_RULES in prompt
    assert "Subagent yang tersedia" not in prompt


# --- (c) urutan blok --------------------------------------------------------


def test_urutan_blok_lengkap_system_narasi_memory_skill_delegasi(monkeypatch):
    """Full-on: SYSTEM -> NARASI -> MEMORI -> SKILL -> DELEGASI."""
    settings = _settings(SKILLS_ENABLED=True, SUBAGENTS_ENABLED=True)
    _, captured = _build_capture(
        "example_chat", settings=settings, monkeypatch=monkeypatch,
        with_memory=True, registry=_registry())
    prompt = captured["system_prompt"]
    i_sys = prompt.index("asisten operasional toko")
    i_nar = prompt.index("Aturan narasi")
    i_mem = prompt.index("Aturan memori user")
    i_skill = prompt.index("Skill yang tersedia")
    i_del = prompt.index("Subagent yang tersedia")
    assert 0 <= i_sys < i_nar < i_mem < i_skill < i_del, (
        f"urutan rusak: sys={i_sys} nar={i_nar} mem={i_mem} "
        f"skill={i_skill} del={i_del}")


def test_urutan_blok_minimal_system_narasi_memory(monkeypatch):
    """Minimal (memori saja): SYSTEM -> NARASI -> MEMORI, tanpa indeks."""
    settings = _settings()
    _, captured = _build_capture(
        "example_chat", settings=settings, monkeypatch=monkeypatch,
        with_memory=True, registry=None)
    prompt = captured["system_prompt"]
    i_sys = prompt.index("asisten operasional toko")
    i_nar = prompt.index("Aturan narasi")
    i_mem = prompt.index("Aturan memori user")
    assert 0 <= i_sys < i_nar < i_mem
    assert "Skill yang tersedia" not in prompt
    assert "Subagent yang tersedia" not in prompt


# --- (d) prefix byte struktural ---------------------------------------------


def test_prefix_tanpa_memori_persis_system_plus_narasi(monkeypatch):
    """Jalur mati: prompt == SYSTEM + '\\n\\n' + NARASI (byte-identik)."""
    from app.agents.ExampleChatAgent import SYSTEM_PROMPT as EX_SYS
    from app.agents.RagChatAgent import SYSTEM_PROMPT as RAG_SYS

    for agent_name, sys_prompt in (("example_chat", EX_SYS),
                                   ("rag_chat", RAG_SYS)):
        _, captured = _build_capture(agent_name, settings=_settings(),
                                     monkeypatch=monkeypatch)
        expected = f"{sys_prompt}\n\n{NARRATION_RULES}"
        assert captured["system_prompt"] == expected, agent_name


def test_prefix_dengan_memori_persis_tambah_memory_rules(monkeypatch):
    """Memori saja: SYSTEM + NARASI + MEMORI (byte-identik, tanpa indeks)."""
    from app.agents.ExampleChatAgent import SYSTEM_PROMPT as EX_SYS

    _, captured = _build_capture(
        "example_chat", settings=_settings(), monkeypatch=monkeypatch,
        with_memory=True, registry=None)
    expected = f"{EX_SYS}\n\n{NARRATION_RULES}\n\n{MEMORY_RULES}"
    assert captured["system_prompt"] == expected


def test_prefix_stabil_di_8_varian_flag(monkeypatch):
    """Prefix SYSTEM+NARASI byte-sama di semua varian (cache stabil)."""
    from app.agents.ExampleChatAgent import SYSTEM_PROMPT as EX_SYS

    prefix = f"{EX_SYS}\n\n{NARRATION_RULES}"
    for skills_on, subagents_on, with_memory in _FLAG_COMBOS:
        settings = _settings(SKILLS_ENABLED=skills_on,
                             SUBAGENTS_ENABLED=subagents_on)
        registry = _registry() if skills_on else None
        _, captured = _build_capture(
            "example_chat", settings=settings, monkeypatch=monkeypatch,
            with_memory=with_memory, registry=registry)
        assert captured["system_prompt"].startswith(prefix), (
            f"skill={skills_on} sub={subagents_on} mem={with_memory}")


def test_indeks_k5_tetap_di_akhir_prompt(monkeypatch):
    """K5 lestari: skill-only -> indeks skill di akhir; full-on -> skill lalu
    delegasi di akhir (narasi tak menggeser indeks ke tengah)."""
    # Skill-only: baris terakhir = entri skill terakhir fixture.
    _, cap_skill = _build_capture(
        "example_chat", settings=_settings(SKILLS_ENABLED=True),
        monkeypatch=monkeypatch, registry=_registry())
    last_skill = cap_skill["system_prompt"].rstrip().rsplit("\n", 1)[-1]
    assert last_skill.startswith("- zebra:"), last_skill

    # Full-on: blok skill sebelum blok delegasi; akhir = indeks delegasi.
    from app.agents.subagents.registry import delegation_index

    _, cap_full = _build_capture(
        "example_chat",
        settings=_settings(SKILLS_ENABLED=True, SUBAGENTS_ENABLED=True),
        monkeypatch=monkeypatch, with_memory=True, registry=_registry())
    prompt = cap_full["system_prompt"]
    assert prompt.index("Skill yang tersedia") < prompt.index(
        "Subagent yang tersedia")
    expected_tail = delegation_index().rstrip().rsplit("\n", 1)[-1]
    assert prompt.rstrip().endswith(expected_tail)


# --- (e) hitl_demo tak kena --------------------------------------------------


def test_hitl_demo_tak_kena_narasi():
    """hitl_demo override build() sendiri — prompt literal tak tersentuh."""
    assert CHAT_AGENTS["hitl_demo"].SYSTEM_PROMPT == "Demo HITL tanpa LLM."
    assert "Aturan narasi" not in CHAT_AGENTS["hitl_demo"].SYSTEM_PROMPT
    assert NARRATION_RULES not in CHAT_AGENTS["hitl_demo"].SYSTEM_PROMPT


# --- (f) bound_tools tak berubah ---------------------------------------------


def test_bound_tools_tak_berubah_oleh_narasi(monkeypatch):
    """Narasi = prompt saja: tool terikat identik baseline gate (tanpa memori
    dan dengan memori), tanpa entri narasi."""
    baselines = {
        (False, "example_chat"): ["check_stock", "process_refund"],
        (False, "rag_chat"): [],
        (True, "example_chat"): ["check_stock", "process_refund",
                                 "save_memory", "save_profile_patch"],
        (True, "rag_chat"): ["save_memory", "save_profile_patch"],
    }
    for (with_memory, agent_name), expected in baselines.items():
        agent, captured = _build_capture(
            agent_name, settings=_settings(), monkeypatch=monkeypatch,
            with_memory=with_memory, registry=None)
        assert agent.bound_tools == expected, (agent_name, with_memory)
        names = [getattr(t, "name", "?") for t in captured["tools"]]
        assert "narasi" not in " ".join(names).lower()
        assert NARRATION_RULES in captured["system_prompt"]
