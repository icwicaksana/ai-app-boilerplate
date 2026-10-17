"""Route jobs: pipeline sync only. Rel pipeline -> JobController.run()."""

from fastapi import APIRouter, Depends, Request

from app.controllers.JobController import JobController
from app.core.dependencies import (
    get_job_controller,
    get_request_id,
    get_thread_identity,
)
from app.core.errors import UpstreamAIError
from app.core.threads import ThreadIdentity
from app.schemas.job import JobRequest, JobResponse

router = APIRouter(tags=["jobs"])


@router.post("/jobs/{agent_name}", response_model=JobResponse)
async def run_job(
    agent_name: str,
    req: JobRequest,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: JobController = Depends(get_job_controller),
) -> JobResponse:
    request_id = get_request_id(request)
    result = await ctrl.run(req, identity, agent_name=agent_name, request_id=request_id)
    if not isinstance(result, JobResponse):
        raise UpstreamAIError("Kontrak JobResponse rusak (bug internal).")
    return result
