"""Ambil teks AIMessage terakhir dari tumpukan messages LangGraph."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

# Tipe block reasoning lintas provider. `redacted_thinking` SENGAJA tidak masuk:
# isinya opaque/terenkripsi, tak layak tampil.
THINKING_BLOCK_TYPES = ("reasoning", "thinking", "reasoning_content")
THINKING_KWARGS_KEYS = ("reasoning_content", "reasoning", "thinking", "reasoning_text")


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


def extract_reasoning_text(msg: Any) -> str:
    """Ambil reasoning/thinking dari 1 pesan (block content + additional_kwargs).

    Diisi model reasoning via ChatLiteLLM (v0.2). Model non-reasoning → "".
    """
    parts: list[str] = []
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") in THINKING_BLOCK_TYPES:
                t = b.get("reasoning") or b.get("text") or b.get("thinking") or ""
                if t:
                    parts.append(str(t))
    kw = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(kw, dict):
        for key in THINKING_KWARGS_KEYS:
            v = kw.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v)
        items = kw.get("reasoning_items")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    t = it.get("summary") or it.get("text") or ""
                    if t:
                        parts.append(str(t))
    return "".join(parts).strip()


def split_chunk_text(chunk: Any) -> tuple[str, str]:
    """Pisah 1 message chunk streaming → (delta_teks, delta_thinking).

    PENTING: delta TIDAK di-strip (spasi antar-chunk bermakna). Kembalikan ""
    untuk sisi yang kosong agar caller bisa skip event berdelta kosong.
    """
    text_parts: list[str] = []
    think_parts: list[str] = []
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype in ("text", "output_text"):
                text_parts.append(str(b.get("text", "")))
            elif btype in THINKING_BLOCK_TYPES:
                t = b.get("reasoning") or b.get("text") or b.get("thinking") or ""
                think_parts.append(str(t))
            # tool_use / redacted_thinking / image / audio: diabaikan di stream teks
    kw = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(kw, dict):
        for key in THINKING_KWARGS_KEYS:
            v = kw.get(key)
            if isinstance(v, str) and v:
                think_parts.append(v)
    return "".join(text_parts), "".join(think_parts)
