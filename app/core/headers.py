"""Middleware security headers (v0.4).

Header aman untuk API JSON (bukan situs): tak ada CSP longgar yang sia-sia,
tapi mencegah MIME sniffing, framing, dan kebocoran referrer.
HSTS HANYA saat `ENVIRONMENT=prod` (dev pakai http).
"""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
}
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool = False):
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in _BASE_HEADERS.items():
            response.headers.setdefault(name, value)
        if self.hsts:
            response.headers.setdefault("Strict-Transport-Security", _HSTS)
        return response
