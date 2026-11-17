"""Paket subagent v0.9 (F4)."""

from app.agents.subagents.registry import (
    SUBAGENTS,
    SubagentSpec,
    delegation_index,
    get_spec,
    register,
)

__all__ = [
    "SUBAGENTS",
    "SubagentSpec",
    "delegation_index",
    "get_spec",
    "register",
]
