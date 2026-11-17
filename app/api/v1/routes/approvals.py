"""API approval + audit (v0.6). Workspace-scoped via JWT.

Keputusan approval **memicu resume graph** — bukan hanya menandai baris.
Semua aksi dicatat ke audit trail (hash chain) sehingga bisa diaudit.
"""

from fastapi import APIRouter, Depends, Query, Request

from app.core.approvals import ApprovalService
from app.core.dependencies import (
    get_approval_service,
    get_request_id,
    get_thread_identity,
    require_scope_dep,
)
from app.core.ratelimit import rate_limit_dependency
from app.core.threads import ThreadIdentity
from app.infra import approvals as repo
from app.schemas.approvals import (
    ApprovalListResponse,
    ApprovalResponse,
    AuditItem,
    AuditListResponse,
    DecideRequest,
    DecideResponse,
    VerifyChainResponse,
)

# v1.0 S4: area reviewer — list/get/decide/audit butuh scope "approve".
# Requester melacak status miliknya via thread miliknya, bukan daftar ini.
# v1.0 S8: satu bucket approvals (decide mahal: resume LLM; verify O(n)).
router = APIRouter(tags=["approvals"],
                   dependencies=[Depends(require_scope_dep("approve")),
                                   Depends(rate_limit_dependency(
                                       "approvals",
                                       "RATE_LIMIT_APPROVALS_PER_MIN"))])


def _row_to_response(row: dict) -> ApprovalResponse:
    data = dict(row)
    for key in ("expires_at", "created_at", "decided_at"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    return ApprovalResponse(**data)


@router.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    identity: ThreadIdentity = Depends(get_thread_identity),
    svc: ApprovalService = Depends(get_approval_service),
    status: str | None = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApprovalListResponse:
    rows = await svc.list(workspace=identity.workspace, status=status,
                          limit=limit, offset=offset)
    return ApprovalListResponse(items=[_row_to_response(r) for r in rows],
                                total=len(rows), limit=limit, offset=offset)


@router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: int,
    identity: ThreadIdentity = Depends(get_thread_identity),
    svc: ApprovalService = Depends(get_approval_service),
) -> ApprovalResponse:
    row = await svc.get(workspace=identity.workspace, approval_id=approval_id)
    return _row_to_response(row)


@router.post("/approvals/{approval_id}/decide", response_model=DecideResponse)
async def decide_approval(
    approval_id: int,
    req: DecideRequest,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    svc: ApprovalService = Depends(get_approval_service),
) -> DecideResponse:
    out = await svc.decide(
        workspace=identity.workspace, actor=identity.user, approval_id=approval_id,
        decision=req.decision, reason=req.reason,
        request_id=get_request_id(request))
    return DecideResponse(**out)


@router.get("/audit", response_model=AuditListResponse)
async def list_audit(
    identity: ThreadIdentity = Depends(get_thread_identity),
    svc: ApprovalService = Depends(get_approval_service),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AuditListResponse:
    rows = await repo.list_audit(svc.pool, workspace=identity.workspace,
                                 limit=limit, offset=offset)
    items = []
    for r in rows:
        data = dict(r)
        if data.get("created_at") is not None:
            data["created_at"] = str(data["created_at"])
        items.append(AuditItem(**data))
    return AuditListResponse(items=items, limit=limit, offset=offset)


@router.get("/audit/verify", response_model=VerifyChainResponse)
async def verify_audit(
    identity: ThreadIdentity = Depends(get_thread_identity),
    svc: ApprovalService = Depends(get_approval_service),
) -> VerifyChainResponse:
    ok, bad = await repo.verify_chain(svc.pool, workspace=identity.workspace)
    return VerifyChainResponse(ok=ok, first_bad_id=bad)
