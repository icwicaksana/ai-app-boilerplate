"""Fake bersama adapter eval canary (deterministik, tanpa I/O)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def eval_settings(**kw):
    from app.core.config import Settings

    base = {"_env_file": None, "LITELLM_BASE_URL": "http://localhost:4000",
            "LITELLM_API_KEY": "x", "LITELLM_MODEL": "m",
            "PHOENIX_ENABLED": False}
    base.update(kw)
    return Settings(**base)


class ScriptedModel(BaseChatModel):
    """Model naskah: tiap generate() = pesan berikut dalam naskah."""

    script: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted-eval"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        idx = self.__dict__.get("_pos", 0)
        object.__setattr__(self, "_pos", idx + 1)  # lewati pydantic
        msg = self.script[min(idx, len(self.script) - 1)]
        out = msg if isinstance(msg, AIMessage) else AIMessage(
            content=str(msg))
        return ChatResult(generations=[ChatGeneration(message=out)])


def tool_call_msg(name: str, args: dict, call_id: str = "call-1") -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": call_id, "type": "tool_call"}])


class ScriptedRetriever:
    """Retriever naskah: hasil per query-substring (default = fallback)."""

    def __init__(self, cases: list[tuple[str, dict]], fallback: dict):
        self._cases = cases
        self._fallback = fallback
        self.seen: list[str] = []

    async def search(self, query, *, k=5, filters=None):
        self.seen.append(query)
        for needle, res in self._cases:
            if needle in (query or ""):
                return dict(res)
        return dict(self._fallback)


def chunk(cid: str, content: str, source: str = "s.md",
          score: float = 0.9) -> dict:
    return {"chunk_id": cid, "doc_id": "d", "content": content,
            "source": source, "page": None, "score": score,
            "backend": "fake", "metadata": {}}


def ok_result(chunks: list, **kw) -> dict:
    res = {"chunks": chunks, "status": "ok", "degraded": [],
           "grounded": True}
    res.update(kw)
    return res


def no_evidence_result() -> dict:
    return {"chunks": [], "status": "no_evidence", "degraded": [],
            "grounded": True}
