"""State dasar untuk StateGraph pipeline kaku (sync, non-streaming).

Chat ReAct (`create_agent`) memakai AgentState internal langchain — BUKAN ini.
Extend BaseState bila butuh kontrol penuh (routing deterministik, multi-step)
tanpa mengubah controller/runner.
"""

from __future__ import annotations

from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class BaseState(TypedDict, total=False):
    """State minimal yang kompatibel dengan runner v0."""

    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Slot multi-tenant/auth (v1): isi dari controller, bukan dari LLM
    metadata: dict[str, Any]
