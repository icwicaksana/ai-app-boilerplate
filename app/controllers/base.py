"""BaseController: thread_id + config + logging. Semua controller extend ini."""

from __future__ import annotations

import time

from app.core.logging import get_logger
from app.core.threads import ThreadIdentity, compose_thread_id, ensure_owner


class BaseController:
    AGENT_NAME = ""

    def __init__(self, settings, checkpointer=None, runner=None, store=None):
        self.settings = settings
        self.checkpointer = checkpointer
        self.runner = runner
        self.store = store  # mentah (AsyncPostgresStore); dibungkus MemoryStore per-request
        self.log = get_logger(self.__class__.__name__)

    @staticmethod
    def new_config(thread_id: str) -> dict:
        if not thread_id:
            raise ValueError("thread_id kosong — controller wajib isi sebelum new_config().")
        return {"configurable": {"thread_id": thread_id}}

    # NOTE: tak ada _ensure_agent generik di sini — tiap rel memakai
    # registry-lookup sendiri (ChatController→CHAT_AGENTS,
    # JobController→JOB_AGENTS); exact-match generik sudah dihapus (dead code).

    def resolve_thread_config(
        self, identity: ThreadIdentity, req_thread_id: str | None
    ) -> tuple[str, dict]:
        """Kosong → compose `tenant:user:sess-*`; isi → parse + cocok pemilik (403).

        Retry dengan id sama = lanjutkan thread, bukan eksekusi ganda.
        """
        if not (req_thread_id or "").strip():
            thread_id = compose_thread_id(identity.tenant, identity.user)
        else:
            ensure_owner(identity, req_thread_id)  # raise 400/403 bila tak valid/asing
            thread_id = req_thread_id.strip().lower()
        return thread_id, self.new_config(thread_id)

    def log_done(self, action: str, *, request_id: str, thread_id: str, agent: str, status: str, started: float) -> None:
        latency_ms = int((time.perf_counter() - started) * 1000)
        self.log.info(
            "%s done request_id=%s thread_id=%s agent=%s status=%s latency_ms=%d",
            action, request_id, thread_id, agent, status, latency_ms,
        )
