"""JobController: rel pipeline only (sync JSON).

- Route /jobs/{agent_name} -> run(req: JobRequest) -> ExamplePipelineAgent.run_agent().
- Tidak ada method stream(): pipeline jalan sekali, pulangkan 1 jawaban utuh.
"""

from __future__ import annotations

import time

from app.agents.AgentContext import AgentContext
from app.agents.BasePipelineAgent import BasePipelineAgent
from app.controllers.base import BaseController
from app.controllers.registry import JOB_AGENTS
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import AgentNotFoundError, UpstreamAIError
from app.core.threads import ThreadIdentity
from app.helpers.response import job_success
from app.schemas.job import JobRequest, JobResponse


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
        self._ensure_agent(name)
        thread_id, config = self.resolve_thread_config(identity, req.thread_id)
        try:
            agent = self._agent_for(thread_id, name)
            out = await agent.run_agent(req.payload, config)
        except ValueError as exc:
            raise UpstreamAIError(str(exc)) from exc
        except UpstreamAIError:
            raise
        except Exception as exc:
            raise UpstreamAIError(f"Eksekusi job gagal: {exc}") from exc
        output = out.get("output", {"reply": ""}) if isinstance(out.get("output"), dict) else {"reply": ""}
        self.log_done("job run", request_id=request_id, thread_id=thread_id,
                      agent=name, status=out.get("status", "completed"), started=started)
        if out.get("status") == "pending_approval":  # v1 (HITL ON)
            job = JobResponse(thread_id=thread_id, status="pending_approval",
                              output=output, interrupt=out.get("interrupt"))
        else:
            job = JobResponse(thread_id=thread_id, status="completed", output=output)
        return job_success(job, request_id=request_id, started=started)
