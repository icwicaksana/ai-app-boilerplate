"""AppError -> HTTP mapping selaras RFC 7807/9457 (Problem Details).

Bentuk error: {type, title, status, detail, instance} + `code` sebagai extension
untuk branching programmatic di FE. `status` SELALU == HTTP status asli.
Controller raise AppError, main terjemahkan. `instance` diisi request_id pemanggil.
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.request_id import resolve_instance
from app.helpers.response import job_error


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        code: str = "INTERNAL_ERROR",
        title: str = "Internal Error",
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.title = title
        self.headers = headers or {}


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


class StorageUnavailableError(AppError):
    def __init__(self, message: str = "Penyimpanan (Postgres) belum siap."):
        super().__init__(message, status_code=503, code="STORAGE_UNAVAILABLE",
                         title="Storage Unavailable")


class BadRequestError(AppError):
    """400 generik untuk input tak-valid di luar kasus spesifik."""

    def __init__(self, message: str):
        super().__init__(message, status_code=400, code="BAD_REQUEST",
                         title="Bad Request")


class InvalidThreadIdError(AppError):
    def __init__(self, thread_id: str = ""):
        super().__init__(
            f"thread_id '{thread_id}' tidak valid — wajib format "
            "'tenant:user:session' (huruf kecil/angka/dash, contoh "
            "'toko-a:budi:sess-7f3a2c1e'). Kosongkan untuk dibuatkan otomatis.",
            status_code=400,
            code="INVALID_THREAD_ID",
            title="Invalid Thread ID",
        )


class ThreadNotFoundError(AppError):
    def __init__(self, thread_id: str = ""):
        super().__init__(
            f"Thread '{thread_id}' tidak ditemukan (sudah dihapus atau belum pernah ada).",
            status_code=404,
            code="THREAD_NOT_FOUND",
            title="Thread Not Found",
        )


class CrossWorkspaceError(AppError):
    def __init__(self):
        super().__init__(
            "Thread milik workspace lain — akses ditolak.",
            status_code=403,
            code="CROSS_TENANT_DENIED",
            title="Forbidden",
        )


class InvalidIdentityError(AppError):
    def __init__(self, detail: str):
        super().__init__(
            f"Identitas request tak valid ({detail}). Periksa klaim JWT: "
            "workspace (3–32 char: huruf kecil/angka/dash) dan sub "
            "(1–64 char: huruf kecil/angka/dash/underscore).",
            status_code=400,
            code="INVALID_IDENTITY",
            title="Invalid Identity",
        )


# --- v0.4: auth / rate limit / idempotency / payload ---

_WWW_BEARER = {"WWW-Authenticate": 'Bearer realm="api"'}


class UnauthenticatedError(AppError):
    """Tidak ada kredensial (401). `WWW-Authenticate` wajib per RFC 7235."""

    def __init__(self, message: str = "Kredensial tidak ada. Kirim "
                                      "`Authorization: Bearer <token>`."):
        super().__init__(message, status_code=401, code="UNAUTHENTICATED",
                         title="Unauthorized", headers=dict(_WWW_BEARER))


class InvalidTokenError(AppError):
    def __init__(self, message: str = "Token tidak valid."):
        super().__init__(message, status_code=401, code="INVALID_TOKEN",
                         title="Unauthorized", headers=dict(_WWW_BEARER))


class TokenExpiredError(AppError):
    def __init__(self, message: str = "Token kedaluwarsa — login/refresh ulang."):
        super().__init__(message, status_code=401, code="TOKEN_EXPIRED",
                         title="Unauthorized", headers=dict(_WWW_BEARER))


class ForbiddenError(AppError):
    def __init__(self, message: str = "Akses ditolak (scope tidak mencukupi)."):
        super().__init__(message, status_code=403, code="FORBIDDEN",
                         title="Forbidden")


class AuthNotConfiguredError(AppError):
    """Mode jwt aktif tapi JWT_SECRET kosong = salah config, bukan salah user."""

    def __init__(self):
        super().__init__(
            "Auth belum terkonfigurasi di server (JWT_SECRET kosong). "
            "Isi JWT_SECRET (min 32 char) di .env lalu restart.",
            status_code=503, code="AUTH_NOT_CONFIGURED",
            title="Auth Not Configured")


class RateLimitedError(AppError):
    def __init__(self, retry_after: int, limit: int):
        super().__init__(
            f"Terlalu banyak permintaan (limit {limit}/menit). "
            f"Coba lagi dalam {max(1, retry_after)} detik.",
            status_code=429, code="RATE_LIMITED", title="Too Many Requests",
            headers={"Retry-After": str(max(1, retry_after)),
                     "X-RateLimit-Limit": str(limit)},
        )


class IdempotencyConflictError(AppError):
    def __init__(self, message: str, code: str = "IDEMPOTENCY_KEY_REUSED"):
        super().__init__(message, status_code=409, code=code,
                         title="Idempotency Conflict")


class PayloadTooLargeError(AppError):
    def __init__(self, size: int, limit: int):
        super().__init__(
            f"Payload {size} byte melebihi batas {limit} byte.",
            status_code=413, code="PAYLOAD_TOO_LARGE",
            title="Payload Too Large")


class StreamGoneError(AppError):
    """Turn stream kedaluwarsa/tidak dikenal (410) — jangan menggantung klien."""

    def __init__(self, turn_id: str = ""):
        super().__init__(
            f"Turn '{turn_id}' tidak tersedia (kedaluwarsa atau belum pernah ada). "
            "Mulai permintaan baru.",
            status_code=410, code="STREAM_GONE", title="Gone")


# --- v0.7: RAG / ingest (desain beku: Docs/RAG-V07-DESIGN.md D12) ---

class DocumentNotFoundError(AppError):
    """Dokumen asing/tak ada → 404 (anti-enumerasi: tanpa bocor workspace)."""

    def __init__(self, document_id: str = ""):
        super().__init__(
            f"Dokumen '{document_id}' tidak ditemukan.",
            status_code=404, code="DOCUMENT_NOT_FOUND",
            title="Document Not Found")


class UnsupportedMediaError(AppError):
    def __init__(self, mime: str = ""):
        super().__init__(
            f"Tipe berkas '{mime}' tidak didukung. Terima: PDF, DOCX, PPTX, "
            "XLSX, Markdown, TXT.",
            status_code=415, code="UNSUPPORTED_MEDIA",
            title="Unsupported Media Type")


class IngestConflictError(AppError):
    """Bentrok idempotency ingest (payload sama sedang diproses / kunci dipakai)."""

    def __init__(self, message: str = "Dokumen sama sedang diproses."):
        super().__init__(message, status_code=409, code="INGEST_CONFLICT",
                         title="Ingest Conflict")


class IngestGoneError(AppError):
    """Job ingest selesai/hilang dari retensi (410) — mulai ingest baru."""

    def __init__(self, job_id: str = ""):
        super().__init__(
            f"Job ingest '{job_id}' tidak tersedia (selesai melewati retensi "
            "atau belum pernah ada). Mulai ingest baru.",
            status_code=410, code="INGEST_GONE", title="Gone")


class EmbeddingUnavailableError(AppError):
    """Service embedding mati. DILARANG fallback beda-model (D6) — 503 eksplisit."""

    def __init__(self, message: str = "Service embedding tidak tersedia. "
                                     "Coba lagi nanti."):
        super().__init__(message, status_code=503,
                         code="EMBEDDING_UNAVAILABLE",
                         title="Embedding Unavailable")


class OcrFailedError(AppError):
    def __init__(self, message: str = "OCR gagal memproses dokumen."):
        super().__init__(message, status_code=422, code="OCR_FAILED",
                         title="OCR Failed")


class ExtractorError(AppError):
    """Ekstraktor entity gagal non-timeout (v0.8 D6: tanpa fallback beda-model)."""

    def __init__(self, message: str = "Ekstraksi entity gagal."):
        super().__init__(message, status_code=503,
                         code="EXTRACTOR_UNAVAILABLE",
                         title="Extractor Unavailable")


class OcrRequiredError(AppError):
    """PDF pindai terdeteksi tapi OCR off — tawarkan, jangan diam-diam OCR (D10)."""

    def __init__(self):
        super().__init__(
            "Dokumen ini hasil pindai (teks terekstrak minim). Ulangi dengan "
            "ocr=on untuk memproses via OCR.",
            status_code=409, code="OCR_REQUIRED",
            title="OCR Required")


class RagBackendMismatchError(AppError):
    """Backend aktif tak cocok dengan data terindeks (D4) — reindex eksplisit."""

    def __init__(self, message: str = "Backend retrieval aktif tidak cocok "
                                     "dengan data terindeks. Jalankan reindex."):
        super().__init__(message, status_code=503,
                         code="RAG_BACKEND_MISMATCH",
                         title="Backend Mismatch")


class RerankError(AppError):
    """Reranker gagal (timeout/koneksi/5xx/skema).

    Kontrak port: `RerankerPort.rerank` me-raise INI (bukan httpx mentah)
    untuk SEMUA kegagalan upstream — `HybridRetriever` menangkapnya menjadi
    jalur degraded (RRF top-k + flag), bukan 500 (P0-2). TimeoutError tetap
    didukung (legacy/adapters lain).
    """

    def __init__(self, message: str = "Reranker tidak tersedia."):
        super().__init__(message, status_code=502, code="RERANK_FAILED",
                         title="Rerank Failed")


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=job_error(
            title=exc.title, detail=exc.message, status=exc.status_code,
            code=exc.code, instance=resolve_instance(request),
        ),
        headers=exc.headers or None,
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
            code="VALIDATION_ERROR", instance=resolve_instance(request),
            extra={"errors": [
                {"loc": [p for p in e.get("loc", ()) if p != "body"],
                 "msg": e.get("msg"), "type": e.get("type")}
                for e in errors
            ]},
        ),
    )
