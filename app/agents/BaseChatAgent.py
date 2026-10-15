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
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agents.AgentContext import AgentContext
from app.agents.utils.MessageUtils import extract_last_ai_text
from app.agents.utils.MiddlewareBuilderUtils import build_middleware
from app.core.logging import get_logger
from app.helpers.response import stream_error, stream_success
from app.infra.llm import get_llm

log = get_logger(__name__)


class BaseChatAgent:
    AGENT_NAME = "base_chat"
    SYSTEM_PROMPT = ""
    TOOLS: list = []

    def __init__(self, ctx: AgentContext):
        self.settings = ctx.settings
        self.checkpointer = ctx.checkpointer
        self.runner = ctx.runner  # inject dari controller — jangan bikin Runner() di sini
        self.graph = self.build(ctx.session_id)

    # --- build: prompt + tools + middleware (jalan 1x saat init) ---
    def build(self, session_id: str | None = None):
        llm = get_llm(self.settings, session_id)
        middleware = build_middleware(self.settings)
        kwargs: dict = {
            "model": llm,
            "tools": list(self.TOOLS),
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

    def _to_state(self, clean: str | dict) -> dict:
        if isinstance(clean, str):
            return {"messages": [HumanMessage(content=clean)]}
        if clean is None:  # pengaman: jangan kirim "null" ke LLM
            return {"messages": [HumanMessage(content="")]}
        return {"messages": [HumanMessage(content=json.dumps(clean, ensure_ascii=False))]}

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

    # --- stream_agent: SSE (dipanggil controller.stream) — SATU-SATUNYA jalur chat ---
    async def stream_agent(self, data: str | dict | None, config: dict):
        clean = self.preprocess(data)
        state = self._to_state(clean)
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
