"""Susun onion middleware agent (v0.2).

Urutan (luar → dalam): [ToolCallLimit, ContextEditing, ToolError].
- ToolCallLimit: rem jumlah tool-call (total + per tool).
- ContextEditing: bersihkan tool-call lama saat konteks melewati ambang.
- ToolError: ubah exception tool jadi ToolMessage ramah agar agen lanjut.

SENGAJA TIDAK ADA: ModelRetry/ModelFallback (milik proxy LiteLLM — hindari
badai retry ganda), Summarization (v0.3), PII (v0.4), HITL aktif (v0.6),
trim_messages manual (digantikan ContextEditing — jangan dobel!).
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Default starter UMKM bila settings tak menyebut (mis. settings palsu di test
# lama) — sumber kebenaran adalah Settings (env-override per klien).
TOOL_CALL_THREAD_LIMIT = 15
TOOL_CALL_PER_TOOL_LIMIT = 5
CONTEXT_TRIGGER_TOKENS = 8000
CONTEXT_KEEP_LAST_TOOL_USES = 3

# v0.7 (P7): tool retrieval boleh dipanggil lebih sering (riset multi-query)
# dan output-nya DIKECUALIKAN dari pembersihan konteks (bukti sitasi harus
# bertahan untuk eval + atribusi; tanpa ini ClearToolUsesEdit menghapus bukti
# yang baru dipakai menjawab).
RETRIEVAL_TOOL_NAMES = frozenset({
    "search_knowledge_base",
    # v0.9 F3: body skill adalah bukti prosedural seperti sitasi RAG — tanpa
    # ini ClearToolUsesEdit menghapus body yang baru dimuat (blocker S1),
    # dan limit 5/giliran mematikan skill di tengah riset (blocker S2).
    "load_skill",
    "read_skill_file",
})
RETRIEVAL_PER_TOOL_LIMIT = 10


def _tool_error_message(exc: Exception, request: Any) -> str:
    name = getattr(request, "tool_name", None) or getattr(request, "name", "tool")
    log.warning("tool %s gagal (%s): %s", name, type(exc).__name__, exc)
    return (
        f"Tool '{name}' gagal ({type(exc).__name__}: {exc}). "
        "Coba cara lain, atau jawab sebisanya tanpa tool itu. "
        "Jangan mengarang data yang seharusnya dari tool."
    )


def build_middleware(settings: Settings, tools: list | None = None) -> list:
    """Bangun daftar middleware sesuai config. Tak pernah raise (fallback [])."""
    stack: list = []
    try:
        from langchain.agents.middleware import (
            ClearToolUsesEdit,
            ContextEditingMiddleware,
            ToolCallLimitMiddleware,
            ToolErrorMiddleware,
        )
    except ImportError as exc:
        log.warning("middleware langchain tak tersedia: %s", exc)
        return []

    thread_limit = getattr(settings, "TOOLCALL_THREAD_LIMIT",
                           TOOL_CALL_THREAD_LIMIT)
    per_tool_limit = getattr(settings, "TOOLCALL_PER_TOOL_LIMIT",
                             TOOL_CALL_PER_TOOL_LIMIT)
    trigger = getattr(settings, "CONTEXT_TRIGGER_TOKENS",
                      CONTEXT_TRIGGER_TOKENS)
    keep = getattr(settings, "CONTEXT_KEEP_LAST_USES",
                   CONTEXT_KEEP_LAST_TOOL_USES)

    # 1. Rem: total per thread + per tool (butuh daftar tools aktual).
    stack.append(ToolCallLimitMiddleware(thread_limit=thread_limit))
    for tool in tools or []:
        # Satu instance per tool-name (semantik langchain: limit per nama);
        # tool tanpa nama dilewati (bukan str(tool) sampah sebagai nama).
        name = getattr(tool, "name", None)
        if not name:
            continue
        limit = (RETRIEVAL_PER_TOOL_LIMIT if str(name) in RETRIEVAL_TOOL_NAMES
                 else per_tool_limit)
        stack.append(ToolCallLimitMiddleware(
            tool_name=str(name), thread_limit=limit))

    # 2. Kebersihan konteks: buang tool-call lama saat > ambang, sisakan N terakhir.
    stack.append(ContextEditingMiddleware(edits=[
        ClearToolUsesEdit(trigger=trigger, keep=keep,
                          exclude_tools=sorted(RETRIEVAL_TOOL_NAMES)),
    ]))

    # 3. Jaring pengaman tool error → ToolMessage (agen lanjut, bukan crash).
    stack.append(ToolErrorMiddleware(on_error=_tool_error_message))

    # 4. HITL (v0.6): approval per tool dengan policy threshold (`when`).
    if getattr(settings, "HITL_ENABLED", False):
        try:
            from langchain.agents.middleware import HumanInTheLoopMiddleware

            from app.core.hitl import interrupt_on_config

            interrupt_on = interrupt_on_config()
            if interrupt_on:
                stack.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
                log.info("HITL aktif untuk tool: %s", sorted(interrupt_on))
            else:
                log.warning("HITL_ENABLED=true tapi HITL_POLICY kosong — dilewati")
        except ImportError as exc:
            log.warning("HITL diminta tapi paket middleware belum tersedia: %s", exc)
    return stack
