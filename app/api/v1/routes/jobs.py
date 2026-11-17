"""Route jobs: pipeline sync only. Rel pipeline -> JobController.run().

v0.4 menambahkan tiga lapis pelindung (urutan penting):
1. Rate limit per identitas (429 + Retry-After).
2. Batas ukuran payload (413) — cegah satu request raksasa.
3. Idempotency-Key (replay tanpa eksekusi / konflik 409).
"""

import json

from fastapi import APIRouter, Depends, Request, Response

from app.controllers.JobController import JobController
from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_cache,
    get_job_controller,
    get_request_id,
    get_thread_identity,
    require_scope_dep,
)
from app.core.errors import (
    AppError,
    IdempotencyConflictError,
    PayloadTooLargeError,
)
from app.core.idempotency import (
    CONFLICT,
    IN_PROGRESS,
    REPLAY,
    IdempotencyGuard,
    body_hash,
)
from app.core.ratelimit import rate_limit_dependency
from app.core.threads import ThreadIdentity
from app.infra.cache import CacheClient
from app.schemas.job import JobRequest, JobResponse

router = APIRouter(tags=["jobs"],
                     dependencies=[Depends(require_scope_dep("jobs"))])

IDEMPOTENCY_HEADER = "Idempotency-Key"


@router.post("/jobs/{agent_name}", response_model=JobResponse)
async def run_job(
    agent_name: str,
    req: JobRequest,
    request: Request,
    response: Response,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: JobController = Depends(get_job_controller),
    settings: Settings = Depends(get_settings),
    cache: CacheClient | None = Depends(get_cache),
    _rl=Depends(rate_limit_dependency("jobs", "RATE_LIMIT_JOBS_PER_MIN")),
) -> JobResponse:
    request_id = get_request_id(request)
    _enforce_payload_cap(req, settings)

    idem_key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
    guard = IdempotencyGuard(cache, settings.IDEMPOTENCY_TTL_SECONDS)
    payload_hash = body_hash({"agent": agent_name, "payload": req.payload,
                              "thread_id": req.thread_id}) if idem_key else ""

    if idem_key:
        decision = await guard.begin(workspace=identity.workspace, user=identity.user,
                                     key=idem_key, payload_hash=payload_hash)
        if decision.state == REPLAY and decision.response is not None:
            response.headers["Idempotent-Replayed"] = "true"
            return JobResponse(**decision.response)
        if decision.state == CONFLICT:
            raise IdempotencyConflictError(
                "Idempotency-Key sudah dipakai untuk payload berbeda.")
        if decision.state == IN_PROGRESS:
            raise IdempotencyConflictError(
                "Request dengan Idempotency-Key ini sedang diproses.",
                code="IDEMPOTENCY_IN_PROGRESS")

    try:
        result = await ctrl.run(req, identity, agent_name=agent_name,
                                request_id=request_id)
        if not isinstance(result, JobResponse):
            # Bug program internal (bukan upstream AI) → 500, bukan 502.
            raise AppError("Kontrak JobResponse rusak (bug internal).",
                           status_code=500, code="INTERNAL_ERROR",
                           title="Internal Error")
    except Exception:
        # Eksekusi gagal = lepas kunci agar klien boleh retry.
        if idem_key:
            await guard.abort(workspace=identity.workspace, user=identity.user,
                              key=idem_key)
        raise

    if idem_key:
        await guard.finish(workspace=identity.workspace, user=identity.user,
                           key=idem_key, payload_hash=payload_hash,
                           response=result.model_dump(mode="json"))
    return result


def _enforce_payload_cap(req: JobRequest, settings: Settings) -> None:
    raw = json.dumps(req.payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(raw) > settings.MAX_JOB_PAYLOAD_BYTES:
        raise PayloadTooLargeError(len(raw), settings.MAX_JOB_PAYLOAD_BYTES)
