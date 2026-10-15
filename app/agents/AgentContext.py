"""AgentContext: satu objek wiring untuk semua agent (hilangkan data-clump).

Dulu tiap __init__ menerima 4 parameter berulang:
`settings, checkpointer, runner, session_id`. Sekarang cukup 1:
`AgentContext(settings, checkpointer, runner, session_id)`.

Controller yang merakit context (session_id = thread_id), agent tinggal pakai.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings


@dataclass
class AgentContext:
    """Wiring agent: config + state backend + pintu LangGraph + sesi percakapan."""

    settings: Settings
    checkpointer: Any
    runner: Any
    session_id: str | None = None
