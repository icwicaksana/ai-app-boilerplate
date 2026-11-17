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
from app.core.errors import (
    AgentNotFoundError,
    AppError,
    UpstreamAIError,
    public_error_detail,
)
from app.core.logging import get_logger
from app.core.threads import ThreadIdentity
from app.helpers.response import stream_error
from app.infra.memory import MemoryStore
from app.schemas.chat import ChatRequest


log = get_logger(__name__)


class ChatController(BaseController):
    AGENT_NAME = "example_chat"  # default; registry = sumber kebenaran

    def __init__(self, settings: Settings, checkpointer, runner: Runner | None = None,
                 store=None, pool=None, rag=None, skills=None):
        super().__init__(settings, checkpointer, runner or Runner(), store=store,
                         pool=pool, rag=rag, skills=skills)

    def _ensure_agent(self, name: str) -> None:
        if name not in CHAT_AGENTS:
            raise AgentNotFoundError(name)

    def _agent_for(self, thread_id: str, agent_name: str) -> BaseChatAgent:
        # Agent dirakit per eksekusi (bukan di __init__) agar session header
        # x-opencode-session = thread_id. Objek controller sendiri sudah per-request,
        # jadi tidak ada biaya tambahan dibanding sebelumnya.
        agent_cls = CHAT_AGENTS[agent_name]
        return agent_cls(self._context_for(agent_cls, thread_id))

    def _context_for(self, agent_cls, thread_id: str):
        """v0.9 F0-5: SATU factory untuk semua jalur (chat/resume/fork).

        `raise_embedding` mempertahankan perilaku v0.7 (D11): agen ber-RAG
        saat RAG mati -> 503 jelas, bukan agen bisu.
        """
        from app.agents.factory import build_agent_context

        return build_agent_context(
            settings=self.settings, checkpointer=self.checkpointer,
            runner=self.runner, session_id=thread_id,
            store=self.store, pool=self.pool, rag=self.rag,
            skills=self.skills,
            agent_cls=agent_cls, missing_retriever="raise_embedding",
        )

    async def stream(self, req: ChatRequest, identity: ThreadIdentity, *,
                     request_id: str = "-"):
        started = time.perf_counter()
        terminal = "completed"  # status AKHIR untuk log (jangan selalu completed)
        try:
            # Resolve + agent-lookup DI SINI (controller tak percaya route):
            # idempotent dengan pre-validate route. Raise AppError (400/403/
            # 404) DITANGKAP → event error terminal (jangan pernah raise dari
            # generator: respons sudah terlanjur 200 — temuan B3).
            thread_id, config = self.resolve_thread_config(identity, req.thread_id)
            self._ensure_agent(req.agent)
        except AppError as exc:
            terminal = "error"
            log.warning("chat stream ditolak pre-run: %s", exc.code)
            yield {"thread_id": req.thread_id or "-", "request_id": request_id,
                   **stream_error(detail=exc.message, title=exc.title,
                                  status=exc.status_code, code=exc.code,
                                  request_id=request_id)}
            return
        try:
            agent = self._agent_for(thread_id, req.agent)
        except UpstreamAIError as exc:
            terminal = "error"
            log.exception("chat init gagal (upstream)")
            yield {"thread_id": thread_id, "request_id": request_id, **stream_error(
                detail=exc.message, title=exc.title, status=exc.status_code,
                code=exc.code, request_id=request_id)}
            return
        except Exception as exc:
            terminal = "error"
            log.exception("chat init gagal")
            yield {"thread_id": thread_id, "request_id": request_id, **stream_error(
                detail=public_error_detail(exc), request_id=request_id)}
            return
        try:
            async for event in agent.stream_agent(req.input, config):
                if event.get("type") == "error":
                    terminal = "error"
                elif event.get("type") == "result" and \
                        event.get("status") == "pending_approval":
                    terminal = "pending_approval"
                yield {"thread_id": thread_id, "request_id": request_id, **event}
        except Exception as exc:  # gagal mid-stream: tutup dengan event error terminal (RFC)
            terminal = "error"
            log.exception("chat stream gagal")
            yield {"thread_id": thread_id, "request_id": request_id, **stream_error(
                detail=public_error_detail(exc), request_id=request_id)}
        finally:
            self.log_done("chat stream", request_id=request_id, thread_id=thread_id,
                          agent=req.agent, status=terminal, started=started)

    def validate_before_stream(self, req: ChatRequest,
                               identity: ThreadIdentity) -> str:
        """Validasi SEBELUM StreamingResponse dibuat (route WAJIB panggil).

        Raise 400/403/404 sebagai JSON biasa — bukan stream 200 terpotong.
        Idempotent dengan cek di stream() (controller tetap validasi ulang).
        """
        thread_id, _ = self.resolve_thread_config(identity, req.thread_id)
        self._ensure_agent(req.agent)
        return thread_id
