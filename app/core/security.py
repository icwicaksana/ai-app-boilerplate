"""Primitif keamanan v0.4: hash password + JWT. MURNI — tanpa I/O jaringan.

Kenapa dipisah: seluruh logika ini bisa diuji tanpa DB/Redis/HTTP, dan
dipakai ulang oleh controller, dependency, dan CLI (create_user).

Keputusan keamanan (ADR-16):
- **Argon2id** via `pwdlib` (bukan passlib — proyek itu tak dirawat dan
  bermasalah dengan bcrypt 4.x).
- **Algoritma JWT DIPIN ke HS256**: `decode` hanya menerima HS256, sehingga
  serangan "alg confusion" (`none`/RS256) tidak mungkin. Secret tak pernah
  dibaca dari tempat lain selain Settings.
- Token punya `typ` (access|refresh): refresh TIDAK bisa dipakai sebagai
  access — dicek di `decode_token(expected_type=...)`.
- Setiap token punya `jti` unik untuk revocation (denylist di cache).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

ALGORITHM = "HS256"  # DIPIN — jangan jadikan konfigurasi bebas
TYPE_ACCESS = "access"
TYPE_REFRESH = "refresh"

_password_hash = PasswordHash.recommended()  # Argon2id (parameter rekomendasi)


# --- password ---

def hash_password(raw: str) -> str:
    """Hash password mentah (Argon2id). Raise ValueError bila kosong."""
    if not raw:
        raise ValueError("password kosong — tolak hash.")
    return _password_hash.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    """Verifikasi password; SELALU bool (hash rusak/tak dikenal → False)."""
    if not raw or not hashed:
        return False
    try:
        return bool(_password_hash.verify(raw, hashed))
    except (UnknownHashError, ValueError, TypeError):
        log.warning("verifikasi password gagal: hash tak dikenal/rusak")
        return False


# --- token ---

# --- Nama klaim identitas ------------------------------------------------
CLAIM_WORKSPACE = "workspace"


class TokenError(Exception):
    """Induk error token (dipetakan ke 401 oleh lapisan API)."""


class TokenExpired(TokenError):
    pass


class InvalidToken(TokenError):
    pass


@dataclass(frozen=True)
class TokenClaims:
    subject: str          # = user
    workspace: str
    jti: str
    token_type: str
    scopes: tuple[str, ...] = field(default_factory=tuple)
    issued_at: int = 0
    expires_at: int = 0
    clearance: str = "public"  # v1.0 G6: level baca maksimum (acl.LEVELS)

    @property
    def ttl_remaining(self) -> int:
        """Sisa umur token (detik) — dipakai TTL denylist."""
        now = int(datetime.now(timezone.utc).timestamp())
        return max(0, self.expires_at - now)


def new_jti() -> str:
    return uuid.uuid4().hex


def _secret(settings: Settings) -> str:
    secret = (settings.JWT_SECRET or "").strip()
    if not secret:
        raise InvalidToken("JWT_SECRET kosong di server")
    return secret


def create_token(
    settings: Settings,
    *,
    workspace: str,
    user: str,
    token_type: str,
    ttl_seconds: int,
    scopes: tuple[str, ...] = (),
    clearance: str = "public",
) -> tuple[str, TokenClaims]:
    """Buat token bertanda tangan + claims-nya (dipakai access & refresh)."""
    from app.core.acl import normalize_level

    if token_type not in (TYPE_ACCESS, TYPE_REFRESH):
        raise ValueError(f"token_type tak dikenal: {token_type!r}")
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl_seconds)
    jti = new_jti()
    clearance = normalize_level(clearance)
    payload = {
        "sub": user,
        "workspace": workspace,
        "typ": token_type,
        "jti": jti,
        "scope": " ".join(scopes),
        "clearance": clearance,
        "iss": settings.JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _secret(settings), algorithm=ALGORITHM)
    claims = TokenClaims(
        subject=user, workspace=workspace, jti=jti, token_type=token_type,
        scopes=tuple(scopes), issued_at=payload["iat"], expires_at=payload["exp"],
        clearance=clearance,
    )
    return token, claims


def create_access_token(settings: Settings, *, workspace: str, user: str,
                        scopes: tuple[str, ...] = (),
                        clearance: str = "public",
                        ) -> tuple[str, TokenClaims]:
    return create_token(settings, workspace=workspace, user=user,
                        token_type=TYPE_ACCESS,
                        ttl_seconds=settings.ACCESS_TOKEN_TTL_SECONDS,
                        scopes=scopes, clearance=clearance)


def create_refresh_token(settings: Settings, *, workspace: str, user: str,
                         scopes: tuple[str, ...] = (),
                         clearance: str = "public",
                         ) -> tuple[str, TokenClaims]:
    return create_token(settings, workspace=workspace, user=user,
                        token_type=TYPE_REFRESH,
                        ttl_seconds=settings.REFRESH_TOKEN_TTL_SECONDS,
                        scopes=scopes, clearance=clearance)


def decode_token(settings: Settings, token: str, *,
                 expected_type: str = TYPE_ACCESS) -> TokenClaims:
    """Verifikasi + validasi klaim. Raise TokenExpired/InvalidToken.

    `algorithms=[ALGORITHM]` = pertahanan alg-confusion; `require` memastikan
    klaim wajib ada (bukan token "kosong" yang lolos).
    """
    if not token:
        raise InvalidToken("token kosong")
    try:
        payload = jwt.decode(
            token,
            _secret(settings),
            algorithms=[ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iat", "jti", "sub", "typ",
                                 CLAIM_WORKSPACE]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("token kedaluwarsa") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken(f"token tidak valid: {exc}") from exc

    if payload.get("typ") != expected_type:
        # Refresh dipakai sebagai access (atau sebaliknya) = tolak.
        raise InvalidToken(
            f"jenis token salah (butuh {expected_type!r}, "
            f"dapat {payload.get('typ')!r})")
    scope_raw = payload.get("scope") or ""
    from app.core.acl import normalize_level

    return TokenClaims(
        subject=str(payload["sub"]),
        workspace=str(payload[CLAIM_WORKSPACE]),
        jti=str(payload["jti"]),
        token_type=str(payload["typ"]),
        scopes=tuple(s for s in str(scope_raw).split(" ") if s),
        issued_at=int(payload.get("iat") or 0),
        expires_at=int(payload.get("exp") or 0),
        # Token pra-G6 tanpa klaim → public (fail-closed, kompatibel).
        clearance=normalize_level(payload.get("clearance")),
    )
