"""Ambil teks AIMessage terakhir dari tumpukan messages LangGraph."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


def coerce_llm_text(content: Any) -> str:
    """Normalisasi content LLM (str | list blocks | None) jadi str bersih.

    Hanya block bertipe teks yang digabung; block tool_use/image/thinking diabaikan.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if content.get("type") in ("text", "output_text"):
            return str(content.get("text", "")).strip()
        return ""
    if isinstance(content, list):
        texts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") in ("text", "output_text")
        ]
        # fallback: block tanpa type tapi ada text (provider lama)
        if not texts:
            texts = [
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and "text" in b and b.get("type") != "tool_use"
            ]
        return "".join(texts).strip()
    return str(content).strip()


def extract_last_ai_text(messages: list) -> str:
    """ToolMessage diabaikan (hanya untuk LLM). Kembalikan "" jika tidak ada AIMessage."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = coerce_llm_text(msg.content)
            if text:
                return text
            # AIMessage dengan tool_calls saja (tanpa teks) → lanjut cari sebelumnya
            continue
    return ""
