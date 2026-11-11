"""RagChatAgent: ReAct chat + tool `search_knowledge_base` (D13).

- Tool factory-bound ke retriever tenant (bukan registry) — pola MemoryTool.
- Reformulasi query di level PROMPT (multi-turn coreference, D9): LLM
  memformulasikan ulang pertanyaan berklausa-gantung menjadi mandiri SEBELUM
  memanggil tool, dengan preservasi entitas/angka.
- postprocess: flag `grounded` heuristik (atribusi); chat TIDAK memaksa
  refusal (percakapan boleh umum) — penolakan tegas hanya di rag_answer.
- Tanpa retriever: degradasi ramah (chat biasa + warning), bukan 503 —
  controller tetap 503-kan bila RAG mati total (lebih jelas di boundary).
"""

from __future__ import annotations

from app.agents.AgentContext import AgentContext
from app.agents.BaseChatAgent import BaseChatAgent
from app.core.logging import get_logger
from app.rag.grounding import REFUSAL_ID, check_attribution

log = get_logger(__name__)

SYSTEM_PROMPT = (
    "Kamu asisten perusahaan yang menjawab dari DOKUMEN internal bila "
    "pertanyaan bersifat faktual. Tool search_knowledge_base mencari bukti; "
    "pakai untuk kebijakan, harga, jadwal, syarat, SOP, dan fakta perusahaan."
)

RAG_RULES = (
    "Aturan RAG: (1) SEBELUM memanggil search_knowledge_base, bila pertanyaan "
    "memakai kata ganti (itu/tersebut/di sana/tadi) yang merujuk pesan "
    "sebelumnya, formulasikan dulu menjadi pertanyaan mandiri LENGKAP — "
    "pertahankan semua nama, angka, tanggal, nominal APA ADANYA (jangan "
    "parafrase nominal), lalu cari dengan pertanyaan lengkap itu. "
    "Contoh: riwayat 'Berapa harga Kopi Arabika?' + 'Kalau yang 100 gram?' → "
    "cari 'Berapa harga Kopi Arabika 100 gram?'. "
    "(2) Setiap kalimat berangka/fakta di jawaban diakhiri marker [^n] sesuai "
    "nomor hasil tool. (3) Bila tool bilang TIDAK ADA BUKTI, jawab PERSIS: "
    f"{REFUSAL_ID} (4) Jangan karang fakta/angka di luar hasil tool."
)


class RagChatAgent(BaseChatAgent):
    AGENT_NAME = "rag_chat"
    NEEDS_RETRIEVER = True
    SYSTEM_PROMPT = SYSTEM_PROMPT
    TOOLS: list = []

    def __init__(self, ctx: AgentContext):
        self.retriever = getattr(ctx, "retriever", None)
        if self.retriever is not None:
            from app.agents.tools.RetrieverTool import (
                make_search_knowledge_tool,
            )

            self.TOOLS = [*self.TOOLS,
                          make_search_knowledge_tool(self.retriever)]
            self.SYSTEM_PROMPT = f"{self.SYSTEM_PROMPT}\n\n{RAG_RULES}"
        else:
            log.warning("rag_chat tanpa retriever — degradasi chat biasa")
        super().__init__(ctx)

    def postprocess(self, reply: str) -> dict:
        """Output {reply, grounded}. `grounded` = heuristik atribusi SEMATA
        (angka↔marker), BUKAN verifikasi semantik — kontrak UI: tampilkan
        badge "perlu verifikasi" bila False (F-11). Chat tak memaksa refusal
        (percakapan boleh umum); penolakan tegas hanya di rag_answer."""
        return {"reply": reply,
                "grounded": not check_attribution(reply or "")}
