"""DEMO structured-output chat (v0.2) — BUKAN agen terdaftar.

Pola: `create_agent(..., response_format=ToolStrategy(Order))`.
Agen bebas menalar, TAPI jawaban final wajib valid sebagai `Order`
( LangChain simpan di `state['structured_response']`).

JANGAN tiru mentah ke CS general: response_format memaksa SETIAP jawaban
jadi skema. Pakai pola ini hanya untuk agen EKSTRAKSI (order, tiket, form).

Sengaja di luar registry (`app/controllers/registry.py`): contoh jalan,
bukan endpoint hidup. Lihat `tests/test_structured_demo.py`.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.factory import ToolStrategy
from pydantic import BaseModel, Field

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.utils.MiddlewareBuilderUtils import build_middleware
from app.infra.llm import get_llm


class OrderItem(BaseModel):
    item_id: str
    quantity: int = Field(ge=1)


class Order(BaseModel):
    customer_name: str
    customer_phone: str
    items: list[OrderItem]
    payment_method: str = Field(
        description="Salah satu: transfer_bank, cod, qris")


ORDER_SYSTEM_PROMPT = (
    "Ekstrak pesanan dari percakapan ke skema Order. "
    "Jangan jawab bebas — SELALU lewat skema. "
    "Jika info kurang, isi string kosong / list kosong, jangan karang."
)


class OrderExtractorDemo(BaseChatAgent):
    AGENT_NAME = "order_extractor_demo"
    SYSTEM_PROMPT = ORDER_SYSTEM_PROMPT
    TOOLS: list = []  # ekstraksi murni, tanpa tools

    def build(self, session_id: str | None = None):
        llm = get_llm(self.settings, session_id)
        middleware = build_middleware(self.settings, [])
        kwargs: dict = {
            "model": llm,
            "tools": [],
            "system_prompt": self.SYSTEM_PROMPT,
            "checkpointer": self.checkpointer,
            # ToolStrategy = via tool-calling (jalan di SEMUA model,
            # termasuk via proxy LiteLLM — tidak butuh native structured).
            "response_format": ToolStrategy(Order),
        }
        if middleware:
            kwargs["middleware"] = middleware
        return create_agent(**kwargs)
