"""JobController: rel pipeline only (sync JSON).

- Route /jobs/{agent_name} -> run(req: JobRequest) -> ExamplePipelineAgent.run_agent().
- Tidak ada method stream(): pipeline jalan sekali, pulangkan 1 jawaban utuh.
- Pipeline SENGAJA tanpa store/memori (deterministik + tanpa personalisasi) —
  bila pipeline butuh memori kelak, teruskan MemoryStore di sini + catat alasannya.
"""

from __future__ import annotations

import time

from app.agents.AgentContext import AgentContext
from app.agents.BasePipelineAgent import BasePipelineAgent
from app.controllers.base import BaseController
from app.controllers.registry import JOB_AGENTS
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import AgentNotFoundError, AppError, UpstreamAIError
from app.core.logging import get_logger
from app.core.threads import ThreadIdentity
from app.helpers.response import job_success
from app.schemas.job import JobRequest, JobResponse


log = get_logger(__name__)


class JobController(BaseController):
    AGENT_NAME = "example_pipeline"  # default; registry = sumber kebenaran

    def __init__(self, settings: Settings, checkpointer, runner: Runner | None = None):
        super().__init__(settings, checkpointer, runner or Runner())

    def _ensure_agent(self, name: str) -> None:
        if name not in JOB_AGENTS:
            raise AgentNotFoundError(name)

    def _agent_for(self, thread_id: str, agent_name: str) -> BasePipelineAgent:
        # Lihat ChatController._agent_for: session header = thread_id.
        ctx = AgentContext(
            settings=self.settings, checkpointer=self.checkpointer,
            runner=self.runner, session_id=thread_id,
        )
        return JOB_AGENTS[agent_name](ctx)

    async def run(
        self,
        req: JobRequest,
        identity: ThreadIdentity,
        *,
        agent_name: str | None = None,
        request_id: str = "-",
    ) -> JobResponse:
        name = agent_name or self.AGENT_NAME
        started = time.perf_counter()
        # Urutan BEKU: 400/403 (resolve thread) SEBELUM 404 (agent) — sama
        # seperti ThreadController (fork melakukan agent-lookup dulu karena
        # butuh graph; di sini resolve dulu karena thread lebih fundamental).
        thread_id, config = self.resolve_thread_config(identity, req.thread_id)
        self._ensure_agent(name)
        try:
            agent = self._agent_for(thread_id, name)
            out = await agent.run_agent(req.payload, config)
        except UpstreamAIError:
            raise
        except (TimeoutError, ConnectionError) as exc:
            raise UpstreamAIError(
                f"Eksekusi job gagal ({type(exc).__name__})") from exc
        except Exception as exc:
            # Bug program / error tak dikenal = 500 INTERNAL — JANGAN 502
            # (502 = upstream AI) dan JANGAN bocorkan nama exception internal
            # ke klien; detail penuh ada di log server via request_id.
            # NOTE: ValueError dari graph TIDAK dipetakan ke 502 (F-08) —
            # parse_amount tak pernah raise; ValueError = bug → 500.
            log.exception("job run gagal tak terduga")
            raise AppError(
                f"Eksekusi job gagal (lihat log request_id={request_id}).",
                status_code=500, code="INTERNAL_ERROR",
                title="Internal Error") from exc
        output = out.get("output", {"reply": ""}) if isinstance(out.get("output"), dict) else {"reply": ""}
        self.log_done("job run", request_id=request_id, thread_id=thread_id,
                      agent=name, status=out.get("status", "completed"), started=started)
        if out.get("status") == "pending_approval":  # v1 (HITL ON)
            job = JobResponse(thread_id=thread_id, status="pending_approval",
                              output=output, interrupt=out.get("interrupt"))
        else:
            job = JobResponse(thread_id=thread_id, status="completed", output=output)
        return job_success(job, request_id=request_id, started=started)
