"""Paket subagent v0.9 (F4)."""

from app.agents.subagents.registry import (
    SUBAGENTS,
    SubagentSpec,
    delegation_index,
    get_spec,
    register,
)

# Contoh hidup (preseden ExampleChatAgent): fork mendapat 1 subagent yang
# benar-benar jalan. Hanya aktif bila SUBAGENTS_ENABLED=true.
from app.agents.subagents import demo as demo  # noqa: F401,E402

__all__ = [
    "SUBAGENTS",
    "SubagentSpec",
    "delegation_index",
    "demo",
    "get_spec",
    "register",
]
