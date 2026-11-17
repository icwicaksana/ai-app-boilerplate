"""Eval skill on-demand v0.9 (F6): 12 positif + 18 adversarial, live LLM.

Mode ganda (preseden test_eval_live): bila gateway LiteLLM mati → SKIP.
Bila hidup → 30 tugas berurutan via graph NYATA (ExampleChatAgent +
distractor tools + registry fixture), suhu 0.

Gate (plan §8):
  recall    = tugas positif yang memuat skill benar / 12   ≥ 0,90
  presisi   = pemuatan benar / seluruh pemuatan            ≥ 0,80
  adversarial = pemuatan di 18 tugas jebakan               = 0
  savings   = 1 − (prompt_indeks + body_dimuat) / prompt_full ≥ 0,30
  (token ≈ char/4 di kedua sisi — estimasi konsisten, bukan absolut)

Sinyal sekunder (dilaporkan, digate longgar): jawaban mengandung ≥1 fakta
khas prosedur (anti-karang) ≥ 0,70 dari tugas positif.
"""

from __future__ import annotations

import asyncio
import urllib.request
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.factory import build_agent_context
from app.controllers.registry import CHAT_AGENTS
from app.core.config import Settings
from app.skills.loader import load_skills
from app.skills.registry import SkillRegistry

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "eval_skills"

# (pertanyaan, skill_harapan, [kata_kunci: salah satu wajib muncul])
POSITIF = [
    ("Laptop saya mati total padahal baru 6 bulan. Bisa klaim garansi? "
     "Syaratnya apa?", "sop-garansi", ["12 bulan", "12", "nota"]),
    ("Berapa lama masa garansi dan SLA penanganannya?", "sop-garansi",
     ["12", "3 hari", "tiga hari"]),
    ("Saya salah pilih ukuran sepatu, bisa tukar? Ketentuannya bagaimana?",
     "sop-retur", ["14 hari", "14", "segel"]),
    ("Kalau retur, uangnya kembali kapan dan ke mana?", "sop-retur",
     ["5 hari", "rekening"]),
    ("Kirim ke Bandung berapa lama sampainya?", "info-pengiriman",
     ["2-3", "2–3", "dua", "tiga", "hari"]),
    ("Belanja berapa biar gratis ongkir? Ada asuransi paket?", "info-pengiriman",
     ["500", "gratis", "0,5", "0.5"]),
    ("Bisa bayar pakai apa saja? Ada cicilan 0 persen?", "info-pembayaran",
     ["QRIS", "BNI", "cicilan", "0 persen", "COD"]),
    ("Transfer bank verifikasinya berapa lama?", "info-pembayaran",
     ["24 jam", "1x24", "WhatsApp"]),
    ("Poin member saya gunanya apa dan cara tukarnya?", "program-member",
     ["10.000", "100 poin", "5.000", "Rp 5"]),
    ("Untungnya jadi member apa? Ada hadiah ulang tahun?", "program-member",
     ["20 persen", "voucher", "silver", "gold"]),
    ("Toko buka sampai jam berapa hari Sabtu?", "jam-operasional",
     ["21.00", "21:00", "jam 9", "pukul 9"]),
    ("Hari libur nasional tokonya buka tidak? CS-nya?", "jam-operasional",
     ["tutup", "H+1", "22.00", "08.00"]),
]

ADVERSARIAL = [
    # basa-basi
    "halo, apa kabar?",
    "terima kasih banyak ya atas bantuannya",
    "kamu AI apa sih?",
    # pengetahuan umum + hitung
    "ibukota Jepang apa?",
    "berapa 17 x 23?",
    "jelaskan fotosintesis secara singkat",
    # terjemahan (termasuk jebakan kata 'retur')
    "translate 'selamat pagi' ke bahasa Inggris",
    "kata 'retur' itu bahasa Inggrisnya apa?",
    # meta tentang skill (jawab dari indeks, JANGAN muat)
    "kamu punya skill apa saja?",
    "kamu bisa bantu apa?",
    # jebakan kata kunci
    "member NCT ada berapa orang?",       # 'member' ≠ program-member
    "tolong kirim salam buat Budi ya",    # 'kirim' ≠ pengiriman
    "jam berapa sekarang?",                # 'jam' ≠ jam-operasional
    # Catatan metodologi (run 1): "menurutmu garansi toko sebelah lebih bagus?"
    # DINILAI AMBIGU dan dikeluarkan — asisten yang baik justru MEMUAT sop
    # garansi untuk membandingkan secara grounded. Tugas adversarial harus
    # punya ground truth tak ambigu (asisten wajar sepakat: tak perlu muat).
    # Opini murni tanpa kait prosedur:
    "menurutmu film apa yang bagus ditonton akhir pekan ini?",
    # coding + umum
    "contoh fungsi python untuk baca file csv",
    "apa itu API? jelaskan untuk pemula",
    "cuaca Jakarta hari ini bagaimana?",
    "siapa presiden pertama Indonesia?",
]


def _gateway_hidup() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:4000/health/liveliness",
                                    timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _settings() -> Settings:
    s = Settings()  # baca .env (LITELLM_*) — integration boleh sentuh env
    s.SKILLS_ENABLED = True
    s.LLM_TEMPERATURE = 0.0
    return s


def _registry() -> SkillRegistry:
    skills, errors = load_skills(FIXTURES)
    assert not errors, errors
    assert len(skills) == 6
    return SkillRegistry.build(skills, root=str(FIXTURES))


def _loads_of(messages) -> list[str]:
    out = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            if call.get("name") == "load_skill":
                out.append(str((call.get("args") or {}).get("name", "")))
    return out


def _last_text(messages) -> str:
    for m in reversed(messages):
        if type(m).__name__ == "AIMessage" and isinstance(m.content, str) \
                and m.content.strip() and not getattr(m, "tool_calls", None):
            return m.content
    return ""


@pytest.mark.asyncio
async def test_eval_30_tugas():
    if not _gateway_hidup():
        pytest.skip("gateway LiteLLM mati — eval LLM dilewati.")
    settings = _settings()
    registry = _registry()
    cls = CHAT_AGENTS["example_chat"]
    ctx = build_agent_context(
        settings=settings, checkpointer=InMemorySaver(), runner=None,
        session_id="eval:toko:skill-1", skills=registry,
        agent_cls=cls, missing_retriever="degrade")
    agent = cls(ctx)
    graph = agent.graph

    base_prompt = cls.SYSTEM_PROMPT
    index = registry.index()
    full_chars = len(base_prompt) + sum(len(s.body) for s in registry.skills)

    rows = []
    for i, (q, expected, keywords) in enumerate(POSITIF):
        tid = f"eval:toko:pos-{i}"
        async with asyncio.timeout(180):
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=q)]},
                config={"configurable": {"thread_id": tid}})
        messages = result["messages"]
        loads = _loads_of(messages)
        answer = _last_text(messages).lower()
        hit = expected in loads
        kw = any(k.lower() in answer for k in keywords)
        loaded_chars = sum(len(registry.get(n).body) for n in set(loads)
                           if registry.get(n) is not None)
        savings = 1 - (len(base_prompt) + len(index) + loaded_chars) / full_chars
        rows.append({"task": f"P{i}", "q": q[:60], "loads": loads,
                     "expected": expected, "hit": hit, "kw": kw,
                     "savings": round(savings, 3)})
        print(f"P{i:02d} loads={loads} hit={hit} kw={kw} "
              f"sav={savings:.2f} | {q[:55]}", flush=True)

    adv_loads = 0
    for j, q in enumerate(ADVERSARIAL):
        tid = f"eval:toko:adv-{j}"
        async with asyncio.timeout(180):
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=q)]},
                config={"configurable": {"thread_id": tid}})
        loads = _loads_of(result["messages"])
        adv_loads += len(loads)
        savings = 1 - (len(base_prompt) + len(index)) / full_chars
        rows.append({"task": f"A{j}", "q": q[:60], "loads": loads,
                     "expected": None, "hit": not loads, "kw": None,
                     "savings": round(savings, 3)})
        print(f"A{j:02d} loads={loads} | {q[:55]}", flush=True)

    recall = sum(1 for r in rows if r["task"].startswith("P") and r["hit"]) / 12
    all_loads = [n for r in rows for n in r["loads"]]
    correct = sum(1 for r in rows if r["task"].startswith("P")
                  for n in r["loads"] if n == r["expected"])
    precision = (correct / len(all_loads)) if all_loads else 1.0
    mean_sav = sum(r["savings"] for r in rows) / len(rows)
    kw_rate = sum(1 for r in rows if r["task"].startswith("P") and r["kw"]) / 12
    print(f"\nHASIL: recall={recall:.2f} presisi={precision:.2f} "
          f"adv_loads={adv_loads} savings={mean_sav:.2f} kw={kw_rate:.2f}",
          flush=True)

    assert recall >= 0.90, f"recall {recall:.2f} < 0,90"
    assert precision >= 0.80, f"presisi {precision:.2f} < 0,80"
    assert adv_loads == 0, f"{adv_loads} load di tugas adversarial"
    assert mean_sav >= 0.30, f"savings {mean_sav:.2f} < 0,30"
    assert kw_rate >= 0.70, f"keyword {kw_rate:.2f} < 0,70 (anti-karang)"
