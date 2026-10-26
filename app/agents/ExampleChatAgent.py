"""ExampleChatAgent: contoh HIDUP chatbot streaming. Kerangka = TemplateChatAgent.py.

Rel chat: route /chat/stream -> ChatController.stream() -> run di sini.
Untuk AI App chat baru: copy TemplateChatAgent.py (kerangka kosong) — atau
copy file ini bila mau mulai dari contoh hidup. Aturan sama untuk pipeline
(TemplatePipelineAgent vs ExamplePipelineAgent): template = kerangka,
example = referensi jalan.
"""

from __future__ import annotations

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.tools.ToolRegistry import TOOLS

SYSTEM_PROMPT = (
    "Anda adalah asisten operasional toko yang membantu dan ringkas. "
    "Jawab dalam Bahasa Indonesia. Jika perlu data stok, gunakan tool yang tersedia. "
    "Jangan mengarang angka di luar hasil tool."
)


class ExampleChatAgent(BaseChatAgent):
    AGENT_NAME = "example_chat"
    SYSTEM_PROMPT = SYSTEM_PROMPT
    TOOLS = TOOLS

    def preprocess(self, data: str | dict | None) -> str | dict:
        """Kustomisasi NYATA contoh ini (beda dari Template yang inherit polos):
        strip + cap 2000 char anti-bloat token. TemplateChatAgent sengaja
        TIDAK override apa pun agar copy-paste mulai dari perilaku base."""
        clean = super().preprocess(data)
        if isinstance(clean, str) and len(clean) > 2000:
            return clean[:2000]
        return clean
