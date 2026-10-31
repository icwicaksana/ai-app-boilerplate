"""Resolusi identitas request (v0.4): JWT (default) atau trust-header (dev).

Satu pintu: `resolve_identity()` dipakai dependency FastAPI, sehingga SEMUA
route (chat/jobs/threads) otomatis aman tanpa perubahan controller —
`ThreadIdentity` tetap objek yang sama, jadi `ensure_owner()` dan seluruh
logika 403/404 v0.3 bekerja apa adanya.

Sikap kegagalan (ADR-16):
- Token invalid/expired/tipe salah → 401 (fail-closed).
- Denylist tak terbaca (cache down) → **izinkan** + log warning (fail-open):
  revoke bersifat best-effort; mematikan seluruh API karena cache mati lebih
  berbahaya daripada jendela revoke yang lebih lebar.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.core.errors import (
    AuthNotConfiguredError,
    ForbiddenError,
    InvalidIdentityError,
    InvalidTokenError,
    TokenExpiredError,
    UnauthenticatedError,
)
from app.core.logging import get_logger
from app.core.security import (
    TYPE_ACCESS,
    InvalidToken,
    TokenClaims,
    TokenExpired,
    decode_token,
)
from app.core.threads import ThreadIdentity, canon_segment
from app.infra.cache import CacheClient, CacheUnavailable, deny_key

log = get_logger(__name__)

BEARER_PREFIX = "bearer "


def _bearer_token(request: Request) -> str:
    raw = request.headers.get("Authorization") or ""
    if not raw.strip():
        raise UnauthenticatedError()
    value = raw.strip()
    if not value.lower().startswith(BEARER_PREFIX):
        raise UnauthenticatedError(
            "Skema Authorization harus `Bearer` (dapat skema lain).")
    token = value[len(BEARER_PREFIX):].strip()
    if not token:
        raise UnauthenticatedError("Token Bearer kosong.")
    return token


async def is_revoked(cache: CacheClient | None, jti: str) -> bool:
    """True bila jti ada di denylist. Cache error → False (fail-open) + log."""
    if cache is None:
        return False
    try:
        return await cache.exists(deny_key(jti))
    except CacheUnavailable:
        log.warning("denylist JWT tak terbaca (cache down) — token diizinkan "
                    "(fail-open, jti=%s)", jti)
        return False


async def revoke(cache: CacheClient | None, claims: TokenClaims) -> bool:
    """Masukkan jti ke denylist dengan TTL = sisa umur token."""
    if cache is None or claims.ttl_remaining <= 0:
        return False
    try:
        await cache.set_json(deny_key(claims.jti), {"typ": claims.token_type},
                             ttl_seconds=claims.ttl_remaining)
        return True
    except CacheUnavailable:
        log.warning("revoke token gagal (cache down) jti=%s", claims.jti)
        return False


def _claims_to_identity(claims: TokenClaims) -> ThreadIdentity:
    """Validasi klaim jadi ThreadIdentity kanonis (pola sama seperti header)."""
    try:
        tenant = canon_segment(claims.tenant, "tenant")
        user = canon_segment(claims.subject, "user")
    except Exception as exc:
        raise InvalidTokenError(
            "Klaim token tak memenuhi format identitas.") from exc
    return ThreadIdentity(tenant=tenant, user=user)


async def resolve_claims(request: Request, settings: Settings,
                         cache: CacheClient | None) -> TokenClaims:
    """Decode + cek denylist. Raise 401/503 (dipakai juga oleh /auth/me)."""
    if settings.AUTH_MODE != "jwt":
        raise UnauthenticatedError(
            "Server berjalan mode trust-header (AUTH_MODE=trusted_headers).")
    if not settings.JWT_SECRET:
        raise AuthNotConfiguredError()
    token = _bearer_token(request)
    try:
        claims = decode_token(settings, token, expected_type=TYPE_ACCESS)
    except TokenExpired as exc:
        raise TokenExpiredError() from exc
    except InvalidToken as exc:
        raise InvalidTokenError(str(exc)) from exc
    if await is_revoked(cache, claims.jti):
        raise InvalidTokenError("Token sudah di-revoke (logout).")
    return claims


async def resolve_identity(request: Request, settings: Settings,
                           cache: CacheClient | None) -> ThreadIdentity:
    """Identitas pemanggil menurut AUTH_MODE. Raise AppError (401/403/503)."""
    if settings.AUTH_MODE == "trusted_headers":
        if not settings.AUTH_ALLOW_TRUSTED_HEADERS:
            raise AuthNotConfiguredError()
        try:
            tenant = canon_segment(request.headers.get("X-Tenant-ID"), "tenant")
            user = canon_segment(request.headers.get("X-User-ID"), "user")
        except Exception as exc:
            raise InvalidIdentityError(str(exc)) from exc
        return ThreadIdentity(tenant=tenant, user=user)
    claims = await resolve_claims(request, settings, cache)
    return _claims_to_identity(claims)


def require_scope(claims: TokenClaims, scope: str) -> None:
    """403 bila scope tak dimiliki. Scope kosong di token = hanya butuh auth."""
    if claims.scopes and scope not in claims.scopes:
        raise ForbiddenError(f"Token tidak punya scope '{scope}'.")
