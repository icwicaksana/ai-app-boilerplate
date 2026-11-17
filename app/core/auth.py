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
- PENGECUALIAN (v1.0 S5): **refresh token fail-closed**. Rotasi tanpa denylist
  = reuse tak terdeteksi selama 7 hari — inti properti keamanannya hilang.
  Refresh saat cache mati → 503, bukan token baru.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.core.degraded import report_degraded
from app.core.errors import (
    AuthNotConfiguredError,
    ForbiddenError,
    InvalidTokenError,
    StorageUnavailableError,
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


async def is_revoked(cache: CacheClient | None, jti: str,
                   request=None) -> bool:
    """True bila jti ada di denylist. Cache error → False (fail-open) + ALARM."""
    if cache is None:
        return False
    try:
        return await cache.exists(deny_key(jti))
    except CacheUnavailable:
        log.warning("denylist JWT tak terbaca (cache down) — token diizinkan "
                    "(fail-open, jti=%s)", jti)
        report_degraded("denylist", "cache_down", request=request)
        return False


async def revoke(cache: CacheClient | None, claims: TokenClaims,
                 request=None) -> bool:
    """Masukkan jti ke denylist dengan TTL = sisa umur token."""
    if cache is None or claims.ttl_remaining <= 0:
        return False
    try:
        await cache.set_json(deny_key(claims.jti), {"typ": claims.token_type},
                             ttl_seconds=claims.ttl_remaining)
        return True
    except CacheUnavailable:
        log.warning("revoke token gagal (cache down) jti=%s", claims.jti)
        report_degraded("revoke", "cache_down", request=request)
        return False


async def claim_refresh(cache: CacheClient | None, claims: TokenClaims) -> None:
    """Klaim atomik SEKALI-PAKAI untuk refresh token (v1.0 S5).

    SET NX: pemenang pertama True, replay/revoke False → 401. Satu round
    trip menggantikan pola cek-lalu-revoke yang racy.
    Cache mati/tak ada → 503 (fail-CLOSED): rotasi tanpa denylist bukan
    rotasi. Pengecualian eksplisit dari fail-open ADR-16 (lihat docstring
    modul) — penerbitan kredensial tak boleh buta.
    """
    if cache is None:
        raise StorageUnavailableError(
            "Layanan refresh butuh cache — coba lagi nanti.")
    try:
        import json as _json

        first = await cache.set_if_absent(
            deny_key(claims.jti),
            _json.dumps({"typ": claims.token_type}),
            max(1, int(claims.ttl_remaining or 0)) if claims.ttl_remaining > 0 else 1)
    except CacheUnavailable as exc:
        log.warning("refresh ditolak: denylist tak bisa ditulis (cache down)")
        raise StorageUnavailableError(
            "Layanan refresh butuh cache — coba lagi nanti.") from exc
    if not first:
        log.warning("refresh ditolak: token sudah dipakai/di-revoke jti=%s",
                    claims.jti)
        raise InvalidTokenError("Refresh token sudah dipakai/di-revoke.")


def _claims_to_identity(claims: TokenClaims) -> ThreadIdentity:
    """Validasi klaim jadi ThreadIdentity kanonis (pola sama seperti header).

    Klaim non-kanonis sudah ditolak di resolve_claims; canon di sini
    pertahanan lapis-2 (tak terjangkau untuk token dari login v1.0+).
    """
    try:
        workspace = canon_segment(claims.workspace, "workspace")
        user = canon_segment(claims.subject, "user")
    except Exception as exc:
        raise InvalidTokenError(
            "Klaim token tak memenuhi format identitas.") from exc
    from app.core.acl import normalize_level

    return ThreadIdentity(workspace=workspace, user=user,
                          clearance=normalize_level(claims.clearance))


def _assert_canonical(claims: TokenClaims) -> None:
    """Tolak klaim non-kanonis (v1.0 S1).

    Login hanya menerbitkan klaim kanonis, jadi `Toko-A`/`Budi` di klaim =
    token pra-perbaikan atau tempaan. Satu pintu di resolve_claims sehingga
    berlaku untuk SEMUA route terautentikasi (termasuk /me).
    """
    try:
        canonical_ws = canon_segment(claims.workspace, "workspace")
        canonical_user = canon_segment(claims.subject, "user")
    except Exception as exc:
        raise InvalidTokenError(
            "Klaim token tak memenuhi format identitas.") from exc
    if claims.workspace != canonical_ws or claims.subject != canonical_user:
        raise InvalidTokenError(
            "Klaim token tak kanonis — login ulang untuk token baru.")


async def resolve_claims(request: Request, settings: Settings,
                         cache: CacheClient | None) -> TokenClaims:
    """Decode + cek denylist. Raise 401/503 (dipakai juga oleh /auth/me)."""
    if not settings.JWT_SECRET:
        raise AuthNotConfiguredError()
    token = _bearer_token(request)
    try:
        claims = decode_token(settings, token, expected_type=TYPE_ACCESS)
    except TokenExpired as exc:
        raise TokenExpiredError() from exc
    except InvalidToken as exc:
        raise InvalidTokenError(str(exc)) from exc
    _assert_canonical(claims)
    if await is_revoked(cache, claims.jti, request=request):
        raise InvalidTokenError("Token sudah di-revoke (logout).")
    return claims


async def resolve_identity(request: Request, settings: Settings,
                           cache: CacheClient | None) -> ThreadIdentity:
    """Identitas pemanggil dari klaim JWT. Raise AppError (401/403/503).

    v0.8.1: jalur trust-header (identitas dari header yang dikirim
    klien) DIHAPUS TOTAL — header bisa dipalsukan siapa pun.
    """
    claims = await resolve_claims(request, settings, cache)
    return _claims_to_identity(claims)


def require_scope(claims: TokenClaims, scope: str) -> None:
    """403 bila scope tak dimiliki. KETAT (v1.0 S4): scope kosong = tolak.

    Semantik lama ("kosong = hanya butuh auth") adalah rasa aman palsu:
    token tanpa scope bisa masuk semua area. Pengecualian hanya untuk
    endpoint yang SENGAJA tanpa gate scope (logout/me/health).
    """
    if scope not in (claims.scopes or ()):
        raise ForbiddenError(f"Token tidak punya scope '{scope}'.")
