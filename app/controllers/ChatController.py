"""ChatController: rel chat only (streaming SSE).

- Route /chat/stream -> stream(req: ChatRequest) -> ExampleChatAgent.stream_agent().
- Tidak ada method run(): chat selalu mengalir. Butuh sync? Lihat JobController.
"""

from __future__ import annotations

import time

from app.agents.AgentContext import AgentContext
from app.agents.BaseChatAgent import BaseChatAgent
from app.controllers.base import BaseController
from app.controllers.registry import CHAT_AGENTS
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import AgentNotFoundError
from app.core.logging import get_logger
from app.core.threads import ThreadIdentity, parse_thread_id
from app.helpers.response import stream_error
from app.infra.memory import MemoryStore
from app.schemas.chat import ChatRequest


log = get_logger(__name__)


class ChatController(BaseController):
    AGENT_NAME = "example_chat"  # default; registry = sumber kebenaran

    def __init__(self, settings: Settings, checkpointer, runner: Runner | None = None,
                 store=None):
        super().__init__(settings, checkpointer, runner or Runner(), store=store)

    def _ensure_agent(self, name: str) -> None:
        if name not in CHAT_AGENTS:
            raise AgentNotFoundError(name)

    def _agent_for(self, thread_id: str, agent_name: str) -> BaseChatAgent:
        # Agent dirakit per eksekusi (bukan di __init__) agar session header
        # x-opencode-session = thread_id. Objek controller sendiri sudah per-request,
        # jadi tidak ada biaya tambahan dibanding sebelumnya.
        # MemoryStore dibungkus per-request (namespace terkunci ke user thread ini).
        memory = None
        if self.store is not None:
            tenant, user, _ = parse_thread_id(thread_id)  # sudah valid (resolve dulu)
            memory = MemoryStore(self.store, tenant, user)
        ctx = AgentContext(
            settings=self.settings, checkpointer=self.checkpointer,
            runner=self.runner, session_id=thread_id, store=memory,
        )
        return CHAT_AGENTS[agent_name](ctx)

    async def stream(self, req: ChatRequest, identity: ThreadIdentity, *,
                     request_id: str = "-"):
        started = time.perf_counter()
        self._ensure_agent(req.agent)
        # Resolve di sini TETAP wajib (controller tak percaya route): idempotent —
        # route sudah pre-resolve untuk JSON 400/403 (lihat routes/chat.py).
        thread_id, config = self.resolve_thread_config(identity, req.thread_id)
        try:
            agent = self._agent_for(thread_id, req.agent)
        except Exception as exc:
            log.exception("chat init gagal")
            yield {"thread_id": thread_id, **stream_error(
                detail=f"{type(exc).__name__}: {exc}", request_id=request_id)}
            return
        try:
            async for event in agent.stream_agent(req.input, config):
                yield {"thread_id": thread_id, "request_id": request_id, **event}
        except Exception as exc:  # gagal mid-stream: tutup dengan event error terminal (RFC)
            log.exception("chat stream gagal")
            yield {"thread_id": thread_id, **stream_error(
                detail=f"{type(exc).__name__}: {exc}", request_id=request_id)}
        finally:
            self.log_done("chat stream", request_id=request_id, thread_id=thread_id,
                          agent=req.agent, status="completed", started=started)
