"""AppError -> HTTP mapping selaras RFC 7807/9457 (Problem Details).

Bentuk error: {type, title, status, detail, instance} + `code` sebagai extension
untuk branching programmatic di FE. `status` SELALU == HTTP status asli.
Controller raise AppError, main terjemahkan. `instance` diisi request_id pemanggil.
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.helpers.response import job_error


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        code: str = "INTERNAL_ERROR",
        title: str = "Internal Error",
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.title = title


class AgentNotFoundError(AppError):
    def __init__(self, agent_name: str):
        super().__init__(
            f"Agent '{agent_name}' tidak terdaftar.",
            status_code=404,
            code="AGENT_NOT_FOUND",
            title="Agent Not Found",
        )


class UpstreamAIError(AppError):
    def __init__(self, message: str = "AI upstream gagal (LLM/tool)."):
        super().__init__(message, status_code=502, code="AI_UPSTREAM_ERROR",
                         title="AI Upstream Error")


class ResumeNotAvailableError(AppError):
    def __init__(self):
        super().__init__(
            "Resume/HITL belum tersedia di v0 (HITL_ENABLED=false). Slot untuk v1.",
            status_code=501,
            code="RESUME_NOT_IMPLEMENTED",
            title="Not Implemented",
        )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=job_error(
            title=exc.title, detail=exc.message, status=exc.status_code,
            code=exc.code, instance=request.headers.get("X-Request-ID", "-"),
        ),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Validasi Pydantic (422) dibentuk sama seperti AppError: RFC 7807 + `code`.

    Detail per-field bawaan FastAPI tidak dibuang — dipindah ke extension `errors`
    agar FE tetap bisa tandai field yang salah, sementara `detail` berisi ringkasan
    1 baris untuk ditampilkan langsung.
    """
    errors = exc.errors()
    shown = errors[:3]
    parts = []
    for e in shown:
        loc = [p for p in e.get("loc", ()) if p != "body"]
        where = ".".join(str(p) for p in loc) or "body"
        parts.append(f"{where}: {e.get('msg', 'tidak valid')}")
    summary = "; ".join(parts) or "Request tidak valid."
    if len(errors) > len(shown):
        summary += f" (+{len(errors) - len(shown)} lainnya)"
    return JSONResponse(
        status_code=422,
        content=job_error(
            title="Validation Error", detail=summary, status=422,
            code="VALIDATION_ERROR", instance=request.headers.get("X-Request-ID", "-"),
            extra={"errors": [
                {"loc": [p for p in e.get("loc", ()) if p != "body"],
                 "msg": e.get("msg"), "type": e.get("type")}
                for e in errors
            ]},
        ),
    )
