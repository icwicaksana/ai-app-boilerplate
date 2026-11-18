"""Tool retrieval RAG: `search_knowledge_base` (factory-bound, D13).

- Pola factory-bound seperti MemoryTool (closure ke 1 BoundRetriever yang
  sudah terikat workspace) — SENGAJA tidak di `ToolRegistry.TOOLS` (M6).
- Output = chunk ter-quoting + marker [^n] + aturan sitasi untuk LLM.
- Fail-soft: gagal → string panduan (agen lanjut), JANGAN raise.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.rag.grounding import REFUSAL_ID, render_quoted_chunk

log = get_logger(__name__)

TOOL_NAME = "search_knowledge_base"

_DESCRIPTION = (
    "Cari fakta di DOKUMEN internal perusahaan (knowledge base). "
    "Pakai untuk pertanyaan faktual (kebijakan, harga, jadwal, syarat, SOP). "
    "Input: pertanyaan lengkap yang mandiri (tanpa kata ganti menggantung). "
    "Output: potongan dokumen ter-quoting bernomor [1..n]. "
    "WAJIB: setiap kalimat berangka/fakta di jawaban diakhiri marker [^n] "
    "sesuai nomor dokumen. Bila tool bilang TIDAK ADA BUKTI, tolak dengan "
    "kalimat persis itu (jangan karang)."
)


def make_search_knowledge_tool(retriever, *, k: int = 5):
    """Ikat tool ke 1 retriever workspace (dipanggil saat build agen chat)."""

    @tool(TOOL_NAME, description=_DESCRIPTION)
    async def search_knowledge_base(query: str) -> str:
        try:
            res = await retriever.search(query, k=k)
        except Exception as exc:  # noqa: BLE001 - fail-soft (MemoryTool)
            log.warning("search_knowledge gagal (%s)", type(exc).__name__)
            return (f"Pencarian dokumen gagal ({type(exc).__name__}). "
                    "JANGAN jawab pertanyaan faktual ini — sampaikan bahwa "
                    "dokumen tak bisa diakses saat ini. Jangan karang fakta.")
        chunks = res.get("chunks") or []
        if res.get("status") == "no_evidence" or not chunks:
            return ("TIDAK ADA BUKTI di dokumen untuk pertanyaan ini. "
                    f"Sampaikan penolakan PERSIS: {REFUSAL_ID}")
        parts = []
        if res.get("grounded") is False or "rerank_unavailable" in (
                res.get("degraded") or []):
            # v1.0 G5: banner eksplisit — urutan RRF lemah tanpa rerank,
            # JANGAN disajikan sebagai bukti kuat.
            parts.append(
                "[PERINGATAN KUALITAS: layanan perangkingan mati — urutan "
                "hasil di bawah ini lemah (skor RRF kasar, tanpa floor). "
                "Hanya jawab bila potongan SANGAT jelas relevan; bila ragu, "
                f"tolak dengan kalimat: {REFUSAL_ID}]")
        for i, c in enumerate(chunks, start=1):
            parts.append(f"[Hasil {i}/{len(chunks)} — sitasi [^{i}]]\n" +
                         render_quoted_chunk(i, str(c.get("source", "")),
                                             str(c.get("content", ""))))
        parts.append(
            "Aturan jawab: (1) HANYA dari DATA di atas; (2) tiap kalimat "
            "berangka/fakta diakhiri [^n]; (3) bila data tak memuat jawaban, "
            f"jawab PERSIS: {REFUSAL_ID}")
        return "\n\n".join(parts)

    return search_knowledge_base
