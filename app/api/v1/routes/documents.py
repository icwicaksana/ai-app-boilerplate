"""Route dokumen/ingest RAG (D12): antre → status → progress → hapus → cari.

Lapis pelindung POST /documents (urut): rate-limit ingest → cap ukuran 413 →
sniff MIME 415 → Idempotency-Key (Valkey fast-path + UNIQUE SoR) → 202.

SSE progress: skema SENDIRI (ingest_started/progress/result/error) — BUKAN
envelope chat (P6). SoR = Postgres (poll 500ms); snapshot saat connect =
resume stateless. Ekor Valkey worker = optimasi latensi masa depan.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import (APIRouter, Depends, File, Form, Query, Request, Response,
                     UploadFile)
from fastapi.responses import StreamingResponse

from app.controllers.DocumentController import DocumentController
from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_cache,
    get_document_controller,
    get_rag_clients,
    get_request_id,
    get_thread_identity,
    require_scope_dep,
)
from app.core.errors import (
    DocumentNotFoundError,
    IdempotencyConflictError,
    IngestConflictError,
    IngestGoneError,
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
from app.schemas.documents import (
    DocumentListResponse,
    DocumentResponse,
    IngestEnqueueResponse,
    JobStatusResponse,
    OcrFlag,
    Preset,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
    RetryRequest,
)

router = APIRouter(tags=["documents"],
                     dependencies=[Depends(require_scope_dep("documents"))])

IDEMPOTENCY_HEADER = "Idempotency-Key"
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_POLL_SECONDS = 0.5
# Terminal SSE: done/dead SAJA (P0-3). `failed` = terjadwal retry (retry_at
# ≤5 mnt) → kirim event retry_scheduled + terus poll, JANGAN tutup stream
# seolah gagal final.
_TERMINAL = ("done", "dead")
_SSE_IDLE_LIMIT = 900  # 15 mnt tanpa perubahan = tutup + suruh reconnect


def _frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _to_doc_response(doc: dict, job: dict | None, chunks: int) -> DocumentResponse:
    return DocumentResponse(
        document_id=str(doc["id"]), title=doc["title"],
        source_type=doc["source_type"], status=doc["status"],
        chunk_preset=doc["chunk_preset"], bytes=doc["bytes"], chunks=chunks,
        job_id=job["id"] if job else None,
        last_error=(job.get("last_error") if job else None),
        created_at=doc.get("created_at"))


@router.post("/documents", response_model=IngestEnqueueResponse,
             status_code=202)
async def upload_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    preset: Preset = Form("prose"),
    ocr: OcrFlag = Form("auto"),
    lang: str = Form("id"),
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
    settings: Settings = Depends(get_settings),
    cache: CacheClient | None = Depends(get_cache),
    _rl=Depends(rate_limit_dependency("ingest", "RATE_LIMIT_INGEST_PER_MIN")),
) -> IngestEnqueueResponse:
    request_id = get_request_id(request)
    blob = await file.read(settings.RAG_MAX_UPLOAD_BYTES + 1)
    if len(blob) > settings.RAG_MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError(len(blob),
                                   settings.RAG_MAX_UPLOAD_BYTES)

    idem_key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
    guard = IdempotencyGuard(cache, settings.IDEMPOTENCY_TTL_SECONDS)
    import hashlib

    payload_hash = body_hash({
        "sha256": hashlib.sha256(blob).hexdigest(), "preset": preset,
        "ocr": ocr}) if idem_key else ""
    # NOTE: filename SENGAJA tak ikut hash (retry wajar boleh beda nama).
    if idem_key:
        decision = await guard.begin(
            workspace=identity.workspace, user=identity.user, key=idem_key,
            payload_hash=payload_hash)
        if decision.degraded:  # v1.0 S9: tanpa dedup -> klien retry hati-hati
            from app.core.degraded import flag_request

            flag_request(request, "idempotency")
        if decision.state == REPLAY and decision.response is not None:
            response.headers["Idempotent-Replayed"] = "true"
            return IngestEnqueueResponse(**decision.response)
        if decision.state == CONFLICT:
            raise IdempotencyConflictError(
                "Idempotency-Key sudah dipakai untuk berkas berbeda.")
        if decision.state == IN_PROGRESS:
            raise IngestConflictError(
                "Upload dengan Idempotency-Key ini sedang diproses.")

    try:
        out = await ctrl.enqueue(
            workspace=identity.workspace, user=identity.user,
            filename=file.filename or "", blob=blob, preset=preset, ocr=ocr,
            lang=(lang or "id")[:8])
    except Exception:
        if idem_key:
            await guard.abort(workspace=identity.workspace, user=identity.user,
                              key=idem_key)
        raise
    body = {"document_id": out["document_id"], "job_id": out["job_id"],
            "status": "queued", "deduped": out["deduped"]}
    if idem_key:
        await guard.finish(workspace=identity.workspace, user=identity.user,
                           key=idem_key, payload_hash=payload_hash,
                           response=body)
    response.headers["X-Request-ID"] = request_id
    return IngestEnqueueResponse(**body)


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
) -> DocumentListResponse:
    if status is not None and status not in (
            "pending", "ready", "failed", "deleted"):
        from app.core.errors import AppError

        # Typo terlihat (422 RFC7807), bukan diam-diam tanpa-filter (P2).
        raise AppError(f"status asing: {status} "
                       "(izinkan: pending/ready/failed/deleted)",
                       status_code=422, code="INVALID_STATUS",
                       title="Invalid Status Filter")
    items, total = await ctrl.list(workspace=identity.workspace, status=status,
                                   limit=limit, offset=offset)
    return DocumentListResponse(
        items=[DocumentResponse(
            document_id=str(d["id"]), title=d["title"],
            source_type=d["source_type"], status=d["status"],
            chunk_preset=d["chunk_preset"], bytes=d["bytes"],
            created_at=d.get("created_at")) for d in items],
        meta={"total": total, "limit": limit, "offset": offset})


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
) -> DocumentResponse:
    detail = await ctrl.get(workspace=identity.workspace, document_id=document_id)
    if detail is None:
        raise DocumentNotFoundError(document_id)
    return _to_doc_response(detail["document"], detail["job"],
                            detail["chunks"])


@router.delete("/documents/{document_id}", status_code=202)
async def delete_document(
    document_id: str,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
    _rl=Depends(rate_limit_dependency("ingest", "RATE_LIMIT_INGEST_PER_MIN")),
) -> dict:
    if not await ctrl.delete(workspace=identity.workspace,
                             document_id=document_id):
        raise DocumentNotFoundError(document_id)
    return {"document_id": document_id, "status": "deleted"}


@router.get("/documents/{document_id}/progress")
async def document_progress(
    document_id: str,
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
    _rl=Depends(rate_limit_dependency("retrieval",
                                      "RATE_LIMIT_RETRIEVAL_PER_MIN")),
) -> StreamingResponse:
    """SSE progress ingest (skema sendiri). Snapshot dulu, lalu poll SoR
    sampai terminal. Job hilang + retensi habis → 410 (via IngestGoneError
    saat connect) — stream putus = connect ulang = snapshot terbaru."""
    detail = await ctrl.get(workspace=identity.workspace, document_id=document_id)
    if detail is None:
        raise DocumentNotFoundError(document_id)
    job = detail["job"]
    if job is None:
        if detail["document"]["status"] == "ready":
            async def _already():
                yield _frame("ingest_result", {
                    "document_id": document_id, "job_id": None,
                    "chunks": detail["chunks"]})
            return StreamingResponse(_already(),
                                     media_type="text/event-stream",
                                     headers=_SSE_HEADERS)
        raise IngestGoneError(document_id)
    job_id = job["id"]

    async def gen():
        yield _frame("ingest_started", {"document_id": document_id,
                                       "job_id": job_id})
        last: tuple = ()
        idle = 0
        tick = 0
        while True:
            if await request.is_disconnected():
                break
            # Guard: dokumen dihapus di tengah stream → tutup jujur (P2).
            cur_doc = await ctrl.get(workspace=identity.workspace,
                                     document_id=document_id)
            if cur_doc is None or cur_doc["document"]["status"] == "deleted":
                yield _frame("ingest_error", {
                    "job_id": job_id, "code": "DOCUMENT_DELETED",
                    "message": "Dokumen dihapus."})
                break
            st = await ctrl.job_status(workspace=identity.workspace, job_id=job_id)
            if st is None:
                yield _frame("ingest_error", {
                    "job_id": job_id, "code": "INGEST_GONE",
                    "message": "Job hilang."})
                break
            sig = (st["state"], st["stage"], st["progress"],
                   st.get("last_error"))
            if sig != last:
                idle = 0
                if st["state"] == "failed":
                    yield _frame("ingest_progress", {
                        "job_id": job_id, "stage": st["stage"],
                        "progress": st["progress"], "state": "failed",
                        "retry_scheduled": True})
                else:
                    yield _frame("ingest_progress", {
                        "job_id": job_id, "stage": st["stage"],
                        "progress": st["progress"], "state": st["state"]})
                last = sig
            else:
                idle += 1
            if st["state"] in _TERMINAL:
                if st["state"] == "done":
                    n = (await ctrl.get(workspace=identity.workspace,
                                        document_id=document_id))["chunks"]
                    yield _frame("ingest_result", {
                        "document_id": document_id, "job_id": job_id,
                        "chunks": n})
                else:
                    yield _frame("ingest_error", {
                        "job_id": job_id, "code": "INGEST_FAILED",
                        "message": st["last_error"] or st["state"]})
                break
            if idle * _POLL_SECONDS >= _SSE_IDLE_LIMIT:
                yield _frame("ingest_error", {
                    "job_id": job_id, "code": "SSE_IDLE_TIMEOUT",
                    "message": "Tak ada progres 15 menit; connect ulang."})
                break
            tick += 1
            if tick % 30 == 0:  # keepalive tiap ~15 dtk (anti proxy-cut)
                yield ": keepalive\n\n"
            await asyncio.sleep(_POLL_SECONDS)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.get("/ingestion_jobs/{job_id}", response_model=JobStatusResponse)
async def get_ingestion_job(
    job_id: int,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
) -> JobStatusResponse:
    st = await ctrl.job_status(workspace=identity.workspace, job_id=job_id)
    if st is None:
        raise IngestGoneError(str(job_id))
    return JobStatusResponse(
        job_id=st["id"], document_id=str(st["document_id"]),
        state=st["state"], stage=st["stage"], progress=st["progress"],
        attempts=st["attempts"], last_error=st.get("last_error"))


@router.post("/ingestion_jobs/{job_id}/retry", status_code=202)
async def retry_ingestion_job(
    job_id: int,
    req: RetryRequest | None = None,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
    _rl=Depends(rate_limit_dependency("ingest", "RATE_LIMIT_INGEST_PER_MIN")),
) -> dict:
    """Reset job failed/dead (+override `ocr`). 409 bila state tak boleh."""
    body = req or RetryRequest()
    job = await ctrl.retry(workspace=identity.workspace, job_id=job_id,
                           ocr=body.ocr)
    if job is None:
        current = await ctrl.job_status(workspace=identity.workspace,
                                        job_id=job_id)
        if current is None:
            raise IngestGoneError(str(job_id))
        raise IngestConflictError(
            f"Job state={current['state']} tak bisa di-retry "
            "(hanya failed/dead).")
    return {"job_id": job_id, "status": "queued"}


@router.post("/retrieval/search", response_model=RetrievalSearchResponse)
async def retrieval_search(
    req: RetrievalSearchRequest,
    response: Response,
    identity: ThreadIdentity = Depends(get_thread_identity),
    ctrl: DocumentController = Depends(get_document_controller),
    clients=Depends(get_rag_clients),
    _rl=Depends(rate_limit_dependency("retrieval",
                                      "RATE_LIMIT_RETRIEVAL_PER_MIN")),
) -> RetrievalSearchResponse:
    """Debug retrieval (filter workspace wajib, dari JWT)."""
    from app.rag import citations as C

    out = await ctrl.search(workspace=identity.workspace, query=req.query, k=req.k,
                            filters=req.filters, clients=clients)
    if out["degraded"]:
        # Observabilitas degradasi untuk operator/FE (P2-13 parsial).
        response.headers["X-RAG-Degraded"] = ",".join(out["degraded"])
    cites = C.build_citations(out["matches"])
    return RetrievalSearchResponse(
        matches=[{k: c[k] for k in
                  ("n", "doc_id", "chunk_id", "source", "page", "score",
                   "backend")} for c in cites],
        meta={"backend": out["backend"], "status": out["status"],
              "degraded": out["degraded"],
              "trace": out.get("trace") or {}})
