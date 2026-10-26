"""STUB v0 untuk HITL resume. v1: teruskan ke runner.resume() via Command(resume=...)."""

from fastapi import APIRouter, Depends, Request

from app.core.dependencies import get_request_id, get_thread_identity
from app.core.errors import ResumeNotAvailableError
from app.core.threads import ThreadIdentity
from app.schemas.job import ResumeRequest

router = APIRouter(tags=["jobs"])


@router.post("/jobs/{thread_id}/resume")
async def resume_job(
    thread_id: str,
    req: ResumeRequest,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
) -> dict:
    _ = (thread_id, req, identity, get_request_id(request))
    raise ResumeNotAvailableError()
