"""BaseChatAgent: induk untuk semua chatbot streaming (ReAct + SSE).

Tujuan: hapus duplikasi ~80% antara ExampleChatAgent vs TemplateChatAgent.
Subclass tinggal set: AGENT_NAME, SYSTEM_PROMPT, TOOLS + override
preprocess/postprocess bila perlu. Jangan override stream_agent/build
kecuali ada alasan kuat.

Kontrak industri (sesuai riset 2025-2026):
- route tipis -> controller -> agent.build(1x) -> runner.stream()
- preprocess murni (tanpa LLM/DB, mudah di-test)
- _safe_postprocess tidak pernah raise (fallback + log)
- stream_agent tahan runner raise (tutup dengan stream_error terminal)

Memori v0.3 (opsional, fail-soft):
- `ctx.store` = MemoryStore per-user (atau None). Bila ada: profil di-load dan
  disisip sebagai SystemMessage; tool `save_memory` terikat otomatis; heuristik
  `extract_memory_writes` menyimpan fakta/`nama` post-turn.
- Memori TAK PERNAH mematahkan chat: semua I/O store dibungkus try/except.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.AgentContext import AgentContext
from app.agents.utils.MessageUtils import extract_last_ai_text
from app.agents.utils.MiddlewareBuilderUtils import build_middleware
from app.core.logging import get_logger
from app.helpers.response import stream_error, stream_success
from app.infra.llm import get_llm
from app.infra.memory import PROFILE_MSG_ID, looks_like_secret, profile_to_prompt_text

log = get_logger(__name__)

# Heuristik deterministik TANPA LLM (scope sadar v0.3; ekstraksi LLM ditunda).
# Keterbatasan: "kamu ingat saya X?" (tanya) ikut tersimpan — diterima sementara.
_REMEMBER_RE = re.compile(
    r"\bingat(?:kan)?\s*(?:ya|kah|dong|tolong)?\s*[:\-]?\s*(.{4,500})",
    re.IGNORECASE | re.DOTALL,
)
_NAME_RE = re.compile(
    r"(?:nama\s+(?:saya|gue|aku)|panggil\s+(?:saya|gue|aku))"
    r"\s+(?:adalah\s+)?([^\n,.!?;]{1,60})",
    re.IGNORECASE,
)
# Partikel ujaran di ekor nama ("Udin ya" → "Udin").
_NAME_TAIL_RE = re.compile(r"\s+(ya|dong|deh|sih|kah|kok|kan|lho|loh)\.?$", re.IGNORECASE)


def extract_memory_writes(text: str) -> dict[str, Any]:
    """Teks user → {"profile": {...}, "facts": [...]}. Murni, mudah di-test.

    Filter rahasia dipakai di SINI (bukan di pemanggil) agar semua jalur tulis
    heuristik otomatis aman — sejajar dengan filter di tool `save_memory`.
    """
    out: dict[str, Any] = {"profile": {}, "facts": []}
    if not isinstance(text, str) or not text.strip():
        return out
    name_m = _NAME_RE.search(text)
    if name_m:
        name = " ".join(name_m.group(1).split())
        name = _NAME_TAIL_RE.sub("", name).strip()
        if len(name) >= 2 and not looks_like_secret(name):
            out["profile"]["nama"] = name
    remember_m = _REMEMBER_RE.search(text)
    if remember_m:
        fact = " ".join(remember_m.group(1).split())
        if len(fact) >= 4 and not looks_like_secret(fact):
            out["facts"].append(fact[:500])
    return out


class BaseChatAgent:
    AGENT_NAME = "base_chat"
    SYSTEM_PROMPT = ""
    TOOLS: list = []

    def __init__(self, ctx: AgentContext):
        self.settings = ctx.settings
        self.checkpointer = ctx.checkpointer
        self.runner = ctx.runner  # inject dari controller — jangan bikin Runner() di sini
        self.memory = getattr(ctx, "store", None)  # MemoryStore per-user / None
        self.graph = self.build(ctx.session_id)

    # --- build: prompt + tools + middleware (jalan 1x saat init) ---
    def build(self, session_id: str | None = None):
        llm = get_llm(self.settings, session_id)
        tools = list(self.TOOLS)
        memory = getattr(self, "memory", None)
        if memory is not None:  # tool terikat user (factory-bound, bukan registry)
            from app.agents.tools.MemoryTool import make_save_memory_tool

            tools.append(make_save_memory_tool(memory))
        middleware = build_middleware(self.settings, tools)
        kwargs: dict = {
            "model": llm,
            "tools": tools,
            "system_prompt": self.SYSTEM_PROMPT,
            "checkpointer": self.checkpointer,
        }
        if middleware:
            kwargs["middleware"] = middleware
        return create_agent(**kwargs)

    # --- preprocess / postprocess: murni, tanpa LLM/DB (mudah di-test) ---
    def preprocess(self, data: str | dict | None) -> str | dict:
        if data is None:
            return ""
        if isinstance(data, str):
            return data.strip()
        return data

    def postprocess(self, reply: str) -> dict:
        return {"reply": reply}

    def _to_state(self, clean: str | dict, profile_text: str = "") -> dict:
        # ID STABIL: add_messages replace profil turn lama (tanpa ini profil
        # menumpuk 1 duplikat per turn di checkpoint — boros token + biaya).
        system = [SystemMessage(content=profile_text, id=PROFILE_MSG_ID)] if profile_text else []
        if isinstance(clean, str):
            return {"messages": [*system, HumanMessage(content=clean)]}
        if clean is None:  # pengaman: jangan kirim "null" ke LLM
            return {"messages": [*system, HumanMessage(content="")]}
        return {"messages": [*system, HumanMessage(
            content=json.dumps(clean, ensure_ascii=False))]}

    def _safe_postprocess(self, reply: str) -> dict:
        try:
            output = self.postprocess(reply)
            if not isinstance(output, dict):
                log.warning("postprocess %s return non-dict, fallback reply", type(self).__name__)
                return {"reply": reply}
            if "reply" not in output:
                log.warning("postprocess %s tanpa key 'reply', fallback reply", type(self).__name__)
                return {"reply": reply, **output}
            return output
        except Exception:
            log.exception("postprocess %s gagal, fallback reply", type(self).__name__)
            return {"reply": reply}

    async def _load_profile_text(self, user_text: str = "") -> str:
        """Profil + fakta relevan → teks prompt ("" bila tanpa memori — fail-soft).

        Profil dan fakta dibaca dalam try TERPISAH: gagal satu tak menggugurkan
        yang lain, dan tak pernah menggugurkan chat.
        """
        memory = getattr(self, "memory", None)
        if memory is None:
            return ""
        try:
            profile = await memory.get_profile()
        except Exception:
            log.exception("load profil gagal (lanjut tanpa profil)")
            profile = {}
        facts: list = []
        if user_text:
            try:
                facts = await memory.search_facts(user_text, limit=3)
            except Exception:
                log.exception("load fakta gagal (lanjut tanpa fakta)")
                facts = []
        return profile_to_prompt_text(profile, facts)

    async def _persist_memory_writes(self, clean: str | dict) -> None:
        """Simpan hasil heuristik post-turn. Tak pernah raise (fail-soft)."""
        memory = getattr(self, "memory", None)
        if memory is None or not isinstance(clean, str):
            return
        try:
            writes = extract_memory_writes(clean)
            if writes["profile"]:
                await memory.put_profile(writes["profile"])
            for fact in writes["facts"][:1]:  # maks 1 fact/turn
                await memory.put_fact(fact)
        except Exception:
            log.exception("simpan memori heuristik gagal (abaikan)")

    # --- stream_agent: SSE (dipanggil controller.stream) — SATU-SATUNYA jalur chat ---
    async def stream_agent(self, data: str | dict | None, config: dict):
        clean = self.preprocess(data)
        user_text = clean if isinstance(clean, str) else ""
        state = self._to_state(clean, await self._load_profile_text(user_text))
        final: dict[str, Any] | None = None
        failed = False
        try:
            stream_iter = self.runner.stream(self.graph, state, config)
            async for event in stream_iter:
                if event.get("type") == "result":
                    final = event
                    reply = extract_last_ai_text(event.get("messages", []))
                    output = self._safe_postprocess(reply)
                    yield stream_success(
                        output=output,
                        status=event.get("status", "completed"),
                        interrupt=event.get("interrupt"),
                    )
                else:
                    if event.get("type") == "error":
                        failed = True  # terminal: JANGAN fallback (cegah eksekusi tool 2x)
                    yield event
        except Exception as exc:  # gagal mid-stream: tutup dengan event error terminal
            log.exception("stream_agent %s gagal", type(self).__name__)
            yield stream_error(detail=f"{type(exc).__name__}: {exc}")
            return
        if final is None and not failed:  # pengaman: runner tak yield result → invoke langsung
            try:
                result = await self.runner.run(self.graph, state, config)
            except Exception as exc:
                log.exception("stream_agent %s fallback run gagal", type(self).__name__)
                yield stream_error(detail=f"{type(exc).__name__}: {exc}")
                return
            reply = extract_last_ai_text(result.get("messages", []))
            output = self._safe_postprocess(reply)
            yield stream_success(
                output=output,
                status=result.get("status", "completed"),
                interrupt=result.get("interrupt"),
            )
        if not failed:  # hanya turn sukses yang boleh menulis memori
            await self._persist_memory_writes(clean)
