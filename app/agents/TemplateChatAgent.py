"""TEMPLATE chatbot streaming (ReAct + SSE). PEDOMAN — jangan dipakai langsung, copy dulu.

Chat = streaming only: template ini SENGAJA hanya punya stream_agent (tanpa run_agent).

Cara membuat AI App chatbot baru (contoh: CsAgent):
  1. Copy file ini → app/agents/CsAgent.py, rename class → CsAgent, AGENT_NAME = "cs".
  2. Isi SYSTEM_PROMPT di bawah (ganti semua [ISI]).
  3. Pilih tools: persempit TOOLS (jangan kasih semua tool ke semua agent).
  4. Sesuaikan preprocess() (bersihkan input) dan postprocess() (bentuk output).
  5. Buat controller (copy ChatController.py) — controller stream() panggil stream_agent().
  6. Route SSE panggil ctrl.stream() — tidak ada jalur sync untuk chat.

Struktur: __init__ build(1x) → stream_agent: preprocess → stream → postprocess.
"""

from __future__ import annotations

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.tools.ToolRegistry import TOOLS as SHARED_TOOLS

# GANTI semua [ISI]. Prompt ikut file ini (copy file = prompt ikut).
SYSTEM_PROMPT = """Kamu adalah [ISI: peran, mis. CS toko elektronik yang ramah].

Tugas: [ISI: mis. jawab stok, ongkir, status pesanan].

Aturan:
- Jawab Bahasa Indonesia, ringkas, maksimal [ISI: mis. 3 kalimat] kecuali diminta detail.
- Jika perlu data, gunakan tool yang tersedia. Jangan mengarang angka di luar hasil tool.
- Jika di luar tugas, tolak sopan dan arahkan ke [ISI: mis. CS manusia]."""


class TemplateChatAgent(BaseChatAgent):
    AGENT_NAME = "template_chat"  # GANTI: mis. "cs"
    SYSTEM_PROMPT = SYSTEM_PROMPT

    # GANTI: pilih subset tools khusus agent ini. Contoh:
    # from app.agents.tools.ExampleSearchTool import check_stock
    # TOOLS = [check_stock]
    TOOLS = list(SHARED_TOOLS)

    # --- preprocess / postprocess: murni, tanpa LLM/DB (mudah di-test) ---
    def preprocess(self, data: str | dict | None) -> str | dict:
        if isinstance(data, str):
            return data.strip()  # SESUAIKAN: mis. lowercase, batasi 2000 char
        return super().preprocess(data)

    def postprocess(self, reply: str) -> dict:
        # SESUAIKAN: mis. {"reply": reply, "citations": [...]}
        return {"reply": reply}
