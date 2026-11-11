"""`TokenizerPort` via HuggingFace (satuan token = tokenizer model, D8).

Lazy import `transformers` di konstruktor (impor modul berat ±detik —
jangan bebankan setiap `import app...`).
Cache: repo-lokal (`RAG_HF_CACHE`, default `.hf-cache`), BUKAN `~/.cache` —
reproducible + ramah container + hindari file-lock profil pengguna.
"""

from __future__ import annotations

import os
from pathlib import Path


class BgeTokenizer:
    """Tokenizer BGE-M3 (XLM-RoBERTa-based, fast). Download ±2MB sekali."""

    def __init__(self, model: str = "BAAI/bge-m3",
                 cache_dir: str | None = None) -> None:
        # Env DULU sebelum import: huggingface_hub membekukan konstanta cache
        # saat import (setenv setelah import = tak berpengaruh, temuan spike).
        target = Path(cache_dir or ".hf-cache").expanduser()
        target.mkdir(parents=True, exist_ok=True)
        # Hormati pilihan operator: jangan timpa HF_HUB_CACHE eksplisit.
        os.environ.setdefault("HF_HUB_CACHE", str(target.resolve()))
        os.environ.setdefault("HF_HOME", str(target.resolve().parent))

        from transformers import AutoTokenizer

        self._name = model
        self._tok = AutoTokenizer.from_pretrained(
            model, trust_remote_code=False,
            cache_dir=os.environ["HF_HUB_CACHE"])

    @property
    def name(self) -> str:
        return self._name

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._tok.encode(text, add_special_tokens=False))

    def truncate_words(self, text: str, max_tokens: int) -> str:
        words = (text or "").split()
        if not words:
            return ""
        if self.count(text) <= max_tokens:
            return text
        # Binary search prefix kata terpanjang yang muat (log(n) encode).
        lo, hi = 0, len(words)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.count(" ".join(words[:mid])) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        return " ".join(words[:lo])
