"""State dasar untuk StateGraph custom (v1+: static flow).

Untuk v0 yang memakai `create_agent`, state internal AgentState sudah cukup.
Extend BaseState agar XxxAgent/orchestrator yang butuh kontrol penuh
(routing deterministik, multi-step workflow) tanpa mengubah controller/runner.
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
