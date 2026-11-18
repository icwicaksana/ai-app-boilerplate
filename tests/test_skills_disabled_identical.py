"""v0.9 F2 gate: SKILLS_ENABLED=false → prompt byte-identik baseline.

Setiap agen chat dibangun dengan skills MATI, lalu system prompt yang
diteruskan ke `create_agent` di-hash. Hash di bawah ini adalah BASELINE yang
diambil saat F2 — sebelum F3 menyentuh `build()` — lalu direbase sadar saat
WS2 (lihat bawah).

Kalau tes ini merah setelah perubahan F3/F4, artinya jalur lama ikut berubah:
perbaiki wiring-nya, JANGAN ganti hash-nya. Hash hanya boleh berubah bila ada
keputusan eksplisit mengubah prompt baseline.

Verifikasi baseline awal (2026-11-17): `git diff 9421e80..HEAD` untuk
BaseChatAgent/RagChatAgent/ExampleChatAgent/memory.py menunjukkan satu-satunya
perubahan adalah penamaan graph F0-3 — konstruksi system prompt tidak
tersentuh. Jadi 4 hash asal setara v0.8.3 murni, bukan pasca-perubahan.

WS2 REBASE SADAR (2026-11-18): keputusan user TERKUNCI — blok NARRATION_RULES
(554 char, always-on tanpa flag) ditempel SETELAH SYSTEM_PROMPT dan SEBELUM
MEMORY_RULES. 4 hash prompt di bawah diregenerasi dengan metode IDENTIK
(sha256[:16] via _build_capture, SKILLS_ENABLED=false). Bukti rebase: prompt
baru minus TEPAT 1 kemunculan "\\n\\n"+NARRATION_RULES (556 char) mengembalikan
4 hash lama v0.8.3 (example 735e61df6882a61c/d5d2aaeb29ded56b, rag
397a3af91b5c8c47/0a450de73732cc22) — diff = 1 blok sisipan, tanpa perubahan
lain. bound_tools + hitl_demo tak berubah (8 tes lain tetap hijau).
"""

from __future__ import annotations

import hashlib

import pytest

from app.agents.BaseChatAgent import NARRATION_RULES
from app.agents.factory import build_agent_context
from app.controllers.registry import CHAT_AGENTS
from app.core.config import Settings

# sha256[:16] atas system prompt lengkap per agen (SKILLS_ENABLED=false).
# Asal: Test/spike_v09/probe_prompt_hash.py pada 2026-11-17 (v0.8.3).
# WS2 (2026-11-18): diregenerasi metode IDENTIK pasca-sisipan NARRATION_RULES
# always-on (SYSTEM -> NARASI -> MEMORI). Bukti: strip 1 blok = hash lama.
BASELINE_PROMPT_SHA = {
    "example_chat": "172a39da87605d47",
    "rag_chat": "315bb8dc2f6a93f7",
}
BASELINE_PROMPT_SHA_MEMORY = {
    "example_chat": "85138deafa9ed382",
    "rag_chat": "31b3c577ebe75b71",
}

# Hash lama v0.8.3 pra-WS2 (bukti rebase): prompt baru minus TEPAT 1 blok
# "\n\n"+NARRATION_RULES harus kembali byte-identik ke hash ini. Jangan ubah;
# kalau tes rebase merah artinya diff != 1 blok sisipan.
OLD_PROMPT_SHA = {
    "example_chat": "735e61df6882a61c",
    "rag_chat": "397a3af91b5c8c47",
}
OLD_PROMPT_SHA_MEMORY = {
    "example_chat": "d5d2aaeb29ded56b",
    "rag_chat": "0a450de73732cc22",
}

# Tool terikat per agen saat skills mati (tanpa memori). Mengunci bahwa F3
# tidak menambah/mengurangi tool di jalur lama.
BASELINE_BOUND_TOOLS = {
    "example_chat": ["check_stock", "process_refund"],
    "rag_chat": [],
}
BASELINE_BOUND_TOOLS_MEMORY = {
    "example_chat": ["check_stock", "process_refund",
                     "save_memory", "save_profile_patch"],
    "rag_chat": ["save_memory", "save_profile_patch"],
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
    agent, captured = _build_capture(agent_name, with_memory=False,
                                     monkeypatch=monkeypatch)
    names = [getattr(t, "name", "?") for t in captured["tools"]]
    assert "load_skill" not in names
    assert "read_skill_file" not in names


@pytest.mark.parametrize("agent_name", sorted(BASELINE_PROMPT_SHA))
def test_nama_graph_dan_bound_tools_terkunci(agent_name, monkeypatch):
    """Gate tidak hanya mengunci teks prompt: identitas graph + tool terikat
    ikut dikunci, supaya perubahan wiring F3/F4 terlihat di sini."""
    agent, captured = _build_capture(agent_name, with_memory=False,
                                     monkeypatch=monkeypatch)
    assert captured["name"] == agent.AGENT_NAME
    assert agent.bound_tools == BASELINE_BOUND_TOOLS[agent_name]
    assert "middleware" in captured


@pytest.mark.parametrize("agent_name", sorted(BASELINE_PROMPT_SHA_MEMORY))
def test_bound_tools_dengan_memori_terkunci(agent_name, monkeypatch):
    agent, _ = _build_capture(agent_name, with_memory=True,
                              monkeypatch=monkeypatch)
    assert agent.bound_tools == BASELINE_BOUND_TOOLS_MEMORY[agent_name]


def test_hitl_demo_prompt_terkunci():
    """hitl_demo tidak lewat build() induk — kuncinya literal, bukan hash."""
    assert CHAT_AGENTS["hitl_demo"].SYSTEM_PROMPT == HITL_DEMO_PROMPT


def test_semua_agen_tercakup_baseline():
    """Agen chat baru yang lupa didaftarkan di sini = baseline tak lengkap."""
    covered = set(BASELINE_PROMPT_SHA) | {"hitl_demo"}
    assert set(CHAT_AGENTS) == covered


@pytest.mark.parametrize("agent_name", sorted(BASELINE_PROMPT_SHA))
@pytest.mark.parametrize("with_memory", [False, True],
                         ids=["tanpa-mem", "dengan-mem"])
def test_ws2_rebase_strip_narasi_kembali_hash_lama(agent_name, with_memory,
                                                  monkeypatch):
    """Kunci bukti rebase WS2: prompt baru minus TEPAT 1 blok narasi = v0.8.3.

    Diff WS2 = 1 blok sisipan "\\n\\n"+NARRATION_RULES, tanpa perubahan lain.
    Kalau tes ini merah, rebase tidak murni (ada edit prompt di luar sisipan).
    """
    _, captured = _build_capture(agent_name, with_memory=with_memory,
                                 monkeypatch=monkeypatch)
    prompt = captured["system_prompt"]
    blok = "\n\n" + NARRATION_RULES
    assert prompt.count(blok) == 1, (
        f"{agent_name} mem={with_memory}: blok narasi muncul "
        f"{prompt.count(blok)}x, harus tepat 1x")
    stripped = prompt.replace(blok, "", 1)
    assert "Aturan narasi" not in stripped
    expected = (OLD_PROMPT_SHA_MEMORY if with_memory
                else OLD_PROMPT_SHA)[agent_name]
    assert _sha(stripped) == expected, (
        f"{agent_name} mem={with_memory}: strip narasi tidak kembali ke "
        f"hash lama v0.8.3")
