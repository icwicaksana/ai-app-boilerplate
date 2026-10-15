"""ExampleChatAgent: contoh HIDUP chatbot streaming. Pedoman jalan = TemplateChatAgent.py.

Rel chat: route /chat/stream -> ChatController.stream() -> run di sini.
Untuk AI App chat baru: copy TemplateChatAgent.py (bukan file ini).
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
        if isinstance(data, str):
            return (data or "").strip()
        return super().preprocess(data)

    def postprocess(self, reply: str) -> dict:
        return {"reply": reply}
