"""Tool `save_memory`: agen menyimpan fakta user saat diminta eksplisit.

- Pola factory-bound (bukan registry global): tool diikat ke 1 MemoryStore
  (namespace user terkunci) saat `BaseChatAgent.build()`. Alasan: stack
  langchain-core 1.6.10 tak punya `ToolRuntime` (hasil spike S-2) — closure
  lebih eksplisit dan tahan versi dibanding injeksi config.
- Fail-soft: gagal tulis → kembalikan pesan gagal (string), JANGAN raise —
  agen lanjut jawab tanpa memori, bukan crash.
- SENGAJA tidak di `ToolRegistry.TOOLS` (registry hanya untuk tool stateless).
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.infra.memory import looks_like_secret

log = get_logger(__name__)

MIN_FACT_CHARS = 4


def make_save_memory_tool(memory):
    """Buat tool `save_memory` yang terikat ke 1 user. `memory`: MemoryStore."""

    @tool("save_memory")
    async def save_memory(text: str) -> str:
        """Simpan fakta tentang user ke memori jangka panjang.

        Pakai HANYA saat user meminta eksplisit ("ingat ya...", "save this...").
        Jangan simpan rahasia (password, OTP, nomor kartu) — tolak dengan sopan.
        """
        clean = (text or "").strip()
        if len(clean) < MIN_FACT_CHARS:
            return "Teks terlalu pendek — sebutkan fakta yang jelas untuk disimpan."
        if looks_like_secret(clean):  # filter bersama heuristik (memory.py)
            return "Maaf, saya tidak menyimpan rahasia seperti password/OTP/kartu."
        try:
            key = await memory.put_fact(clean)
            log.info("save_memory tersimpan key=%s", key)
            return "Tersimpan di memori."
        except Exception as exc:
            log.exception("save_memory gagal")
            return f"Gagal menyimpan memori ({type(exc).__name__}). Lanjut tanpa menyimpan."

    return save_memory
