"""Korelasi request_id: middleware + contextvars + helper instance.

Satu-satunya sumber kebenaran request_id:
- `RequestIDMiddleware` (dipasang di create_app): echo `X-Request-ID` atau
  generate 12hex → simpan di `request.state.request_id` + ContextVar (untuk
  log) → set header respons `X-Request-ID`.
- `resolve_instance(request)`: dipakai SEMUA error handler — tak pernah "-".
  Tanpa header pun pemanggil dapat ID hasil generate (lihat header respons).
"""

from __future__ import annotations

import contextvars
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Tempel request_id ke state + log-context + header respons."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


def resolve_instance(request: Request | None) -> str:
    """request_id untuk envelope error. Tak pernah kosong/"-".

    Prioritas: state (middleware) → header (echo) → generate baru.
    """
    if request is not None:
        state_rid = getattr(getattr(request, "state", None), "request_id", None)
        if state_rid:
            return str(state_rid)
        header_rid = request.headers.get("X-Request-ID") if hasattr(
            request, "headers") else None
        if header_rid:
            return str(header_rid)
    rid = uuid.uuid4().hex[:12]
    if request is not None:
        try:
            request.state.request_id = rid
        except Exception:
            pass
    return rid
