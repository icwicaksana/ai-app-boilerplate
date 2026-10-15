"""BasePipelineAgent: induk untuk pipeline kaku (StateGraph sync, non-streaming).

Tujuan: hapus duplikasi 100% run_agent/_to_state antara
ExamplePipelineAgent vs TemplatePipelineAgent.
Subclass tinggal set: AGENT_NAME + State + node validate/calculate/narrate.

Pola industri: deterministik (validasi/hitung) = Python murni TANPA LLM;
hanya node terakhir boleh LLM (narasi). 1 transaksi = 1 thread_id.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.AgentContext import AgentContext
from app.agents.utils.MessageUtils import coerce_llm_text, extract_last_ai_text
from app.core.logging import get_logger
from app.infra.llm import get_llm

log = get_logger(__name__)


class BasePipelineAgent(ABC):
    AGENT_NAME = "base_pipeline"
    FINAL_KEY = "final"

    def __init__(self, ctx: AgentContext):
        self.settings = ctx.settings
        self.checkpointer = ctx.checkpointer
        self.runner = ctx.runner  # inject dari controller — jangan bikin Runner() di sini
        self.llm = get_llm(ctx.settings, ctx.session_id)  # dipakai node LLM terakhir saja
        self.graph = self.build()

    @abstractmethod
    def build(self):
        """Denah StateGraph kaku. Subclass wajib implement (dipanggil 1x saat init)."""
        raise NotImplementedError

    def _to_state(self, clean: dict) -> dict:
        if not isinstance(clean, dict):
            clean = {}
        try:
            content = json.dumps(clean, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            log.warning("pipeline %s _to_state JSON gagal, pakai {}", type(self).__name__)
            clean = {}
            content = "{}"
        return {
            "raw_input": clean,
            "messages": [HumanMessage(content=content)],
        }

    def _extract_final(self, raw: Any) -> str | None:
        if isinstance(raw, dict):
            final = raw.get(self.FINAL_KEY)
            if isinstance(final, str) and final.strip():
                return final
        return None

    @staticmethod
    def _wrap_narrate_response(resp: Any) -> Any:
        """Pastikan pesan narrate selalu BaseMessage (test/fake LLM bisa return str)."""
        if isinstance(resp, (HumanMessage, AIMessage)):
            return resp
        try:
            from langchain_core.messages import BaseMessage

            if isinstance(resp, BaseMessage):
                return resp
        except ImportError:
            pass
        return AIMessage(content=coerce_llm_text(getattr(resp, "content", resp)))

    # --- run_agent: one-shot async non-streaming (dipanggil JobController.run) ---
    async def run_agent(self, data: dict, config: dict) -> dict[str, Any]:
        if not isinstance(data, dict):
            log.warning("pipeline %s run_agent data non-dict (%s), pakai {}",
                        type(self).__name__, type(data).__name__)
            data = {}
        state = self._to_state(dict(data or {}))
        result = await self.runner.run(self.graph, state, config)
        if not isinstance(result, dict):
            result = {"status": "completed", "messages": [], "raw": {}}
        messages = result.get("messages", []) or []
        reply = self._extract_final(result.get("raw")) or extract_last_ai_text(messages)
        if not reply or not reply.strip():
            log.warning("pipeline %s hasil kosong (graph=%s)", type(self).__name__, type(self.graph).__name__)
            reply = ""
        output: dict[str, Any] = {"reply": reply}
        raw = result.get("raw")
        if isinstance(raw, dict) and isinstance(raw.get("structured"), dict):
            # Aditif (v0.2): pipeline ber-structured output sertakan data tervalidasi.
            # Pipeline tanpa itu (Template) output-nya tetap {"reply"} seperti v0.
            output["data"] = raw["structured"]
        return {
            "status": result.get("status", "completed"),
            "output": output,
            "interrupt": result.get("interrupt"),
            "messages": messages if isinstance(messages, list) else [],
        }
