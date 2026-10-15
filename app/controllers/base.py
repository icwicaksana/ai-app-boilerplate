"""BaseController: thread_id + config + logging. Semua controller extend ini."""

from __future__ import annotations

import time
import uuid

from app.core.errors import AgentNotFoundError
from app.core.logging import get_logger


class BaseController:
    AGENT_NAME = ""

    def __init__(self, settings, checkpointer=None, runner=None):
        self.settings = settings
        self.checkpointer = checkpointer
        self.runner = runner
        self.log = get_logger(self.__class__.__name__)

    @staticmethod
    def new_thread_id(prefix: str = "thr") -> str:
        """Retry dengan id sama = lanjutkan, bukan duplikat."""
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def new_config(thread_id: str) -> dict:
        if not thread_id:
            raise ValueError("thread_id kosong — controller wajib isi sebelum new_config().")
        return {"configurable": {"thread_id": thread_id}}

    def _ensure_agent(self, name: str) -> None:
        if name != self.AGENT_NAME:
            raise AgentNotFoundError(name)

    def resolve_thread_config(self, req_thread_id: str | None, prefix: str) -> tuple[str, dict]:
        thread_id = req_thread_id or self.new_thread_id(prefix)
        return thread_id, self.new_config(thread_id)

    def log_done(self, action: str, *, request_id: str, thread_id: str, agent: str, status: str, started: float) -> None:
        latency_ms = int((time.perf_counter() - started) * 1000)
        self.log.info(
            "%s done request_id=%s thread_id=%s agent=%s status=%s latency_ms=%d",
            action, request_id, thread_id, agent, status, latency_ms,
        )
