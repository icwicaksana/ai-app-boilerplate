"""AuthController (v0.4): login / refresh / logout / me.

Kontrak keamanan:
- Pesan gagal login SELALU generik (jangan bocorkan user ada/tidak atau
  password salah) — cegah enumerasi akun.
- Refresh = **rotasi**: token lama masuk denylist sebelum token baru terbit,
  sehingga refresh yang dicuri hanya bisa dipakai sekali.
- Logout me-revoke access + refresh (bila dikirim) selama sisa umurnya.
"""

from __future__ import annotations

import time

from app.controllers.base import BaseController
from app.core.auth import claim_refresh, revoke
from app.core.config import Settings
from app.core.errors import (
    InvalidTokenError,
    TokenExpiredError,
    UnauthenticatedError,
)
from app.core.logging import get_logger
from app.core.security import (
    TYPE_REFRESH,
    InvalidToken,
    TokenClaims,
    TokenExpired,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.infra import users
from app.infra.cache import CacheClient
from app.schemas.auth import MeResponse, TokenPair

log = get_logger(__name__)

_GENERIC_LOGIN_FAIL = "Kredensial tidak valid."


class AuthController(BaseController):
    def __init__(self, settings: Settings, pool, cache: CacheClient | None = None):
        super().__init__(settings, None, None, store=None)
        self.pool = pool
        self.cache = cache

    # --- login ---
    async def login(self, *, workspace: str, username: str, password: str,
                    request_id: str = "-") -> TokenPair:
        started = time.perf_counter()
        # v1.0 S1: normalisasi di controller (skema route sudah lowercase-only,
        # ini pertahanan lapis-2 untuk pemanggil langsung).
        workspace, username = users.canon_identity(workspace, username)
        user = await users.get_user(self.pool, workspace, username)
        # Verifikasi tetap dijalankan walau user tak ada? Tidak perlu: timing
        # oracle kecil diterima, pesan tetap generik (fokus anti-enumerasi).
        if not user or not user.get("is_active"):
            log.warning("login gagal request_id=%s workspace=%s user=%s reason=unknown",
                        request_id, workspace, username)
            raise UnauthenticatedError(_GENERIC_LOGIN_FAIL)
        if not verify_password(password, str(user["password_hash"])):
            log.warning("login gagal request_id=%s workspace=%s user=%s reason=password",
                        request_id, workspace, username)
            raise UnauthenticatedError(_GENERIC_LOGIN_FAIL)

        scopes = tuple(s for s in str(user.get("scopes") or "").split() if s)
        pair = self._issue_pair(workspace=workspace, user=username, scopes=scopes)
        await users.mark_login(self.pool, int(user["id"]))
        self.log_done("auth login", request_id=request_id,
                      thread_id="-", agent="-", status="completed", started=started)
        return pair

    # --- refresh (rotasi) ---
    async def refresh(self, refresh_token: str, *, request_id: str = "-") -> TokenPair:
        claims = self._decode_refresh(refresh_token)
        # v1.0 S5: klaim atomik SEKALI-PAKAI (SET NX) menggantikan cek-lalu-
        # revoke yang racy. Gagal klaim = replay/revoke → 401; cache mati →
        # 503 (fail-closed, bukan token buta).
        await claim_refresh(self.cache, claims)
        user = await users.get_user(self.pool, claims.workspace, claims.subject)
        if not user or not user.get("is_active"):
            raise InvalidTokenError("User tidak aktif.")
        scopes = tuple(s for s in str(user.get("scopes") or "").split() if s)
        pair = self._issue_pair(workspace=claims.workspace, user=claims.subject,
                                scopes=scopes)
        log.info("refresh ok request_id=%s jti_lama=%s", request_id, claims.jti)
        return pair

    # --- logout ---
    async def logout(self, claims: TokenClaims, *,
                     refresh_token: str | None = None,
                     request_id: str = "-") -> None:
        revoked_access = await revoke(self.cache, claims)
        revoked_refresh = False
        if refresh_token:
            try:
                rc = decode_token(self.settings, refresh_token,
                                  expected_type=TYPE_REFRESH)
                revoked_refresh = await revoke(self.cache, rc)
            except (TokenExpired, InvalidToken):
                # Token refresh sudah tak valid = tak ada yang perlu di-revoke.
                revoked_refresh = True
        log.info("logout request_id=%s access_revoked=%s refresh_revoked=%s",
                 request_id, revoked_access, revoked_refresh)

    # --- me ---
    def me(self, claims: TokenClaims) -> MeResponse:
        return MeResponse(workspace=claims.workspace, user=claims.subject,
                          scopes=list(claims.scopes),
                          expires_at=claims.expires_at)

    # --- internal ---
    def _issue_pair(self, *, workspace: str, user: str,
                    scopes: tuple[str, ...]) -> TokenPair:
        access, _ = create_access_token(self.settings, workspace=workspace, user=user,
                                        scopes=scopes)
        refresh, _ = create_refresh_token(self.settings, workspace=workspace, user=user,
                                          scopes=scopes)
        return TokenPair(access_token=access, refresh_token=refresh,
                         expires_in=self.settings.ACCESS_TOKEN_TTL_SECONDS)

    def _decode_refresh(self, token: str) -> TokenClaims:
        try:
            return decode_token(self.settings, token, expected_type=TYPE_REFRESH)
        except TokenExpired as exc:
            raise TokenExpiredError("Refresh token kedaluwarsa — login ulang.") from exc
        except InvalidToken as exc:
            raise InvalidTokenError(str(exc)) from exc
