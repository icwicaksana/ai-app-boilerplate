"""STUB v0 untuk HITL resume. v1: teruskan ke runner.resume() via Command(resume=...)."""

from fastapi import APIRouter

from app.core.errors import ResumeNotAvailableError
from app.schemas.job import ResumeRequest

router = APIRouter(tags=["jobs"])


@router.post("/jobs/{thread_id}/resume")
async def resume_job(thread_id: str, req: ResumeRequest) -> dict:
    _ = (thread_id, req)
    raise ResumeNotAvailableError()
