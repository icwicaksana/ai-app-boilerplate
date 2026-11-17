"""Rate limit fixed-window di cache (v0.4, ADR-16).

Kenapa fixed-window: atomik dengan satu perintah (INCR+EXPIRE via Lua),
mudah diaudit, dan cukup untuk melindungi biaya LLM. Token-bucket lebih
halus tapi butuh state tambahan — ditunda sampai ada kebutuhan nyata.

Sikap kegagalan: **fail-open**. Cache mati = permintaan DIIZINKAN dengan
penanda `degraded` (log warning). Memblokir seluruh API karena cache mati
lebih merugikan daripada kehilangan pembatasan sesaat.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger
from app.core.threads import ThreadIdentity
from app.infra.cache import CacheClient, CacheUnavailable, rate_limit_key

log = get_logger(__name__)

WINDOW_SECONDS = 60


def client_ip(request: Request, settings: Settings) -> str:
    """IP klien sebenarnya dengan allowlist proxy tepercaya (v1.0 S8).

    - Peer langsung selalu dipakai bila tak ada proxy tepercaya (default:
      TRUSTED_PROXY_CIDRS kosong = abaikan XFF total → anti-spoof).
    - Bila peer ∈ CIDR tepercaya dan X-Forwarded-For ada → entri KIRI
      (klien asli di rantai proxy yang kita kendalikan).
    - Entri CIDR invalid diabaikan + warning (fail-closed: tak dipercaya).
    """
    peer = getattr(getattr(request, "client", None), "host", None) or "unknown"
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if not xff:
        return peer
    cidrs = [c.strip() for c in (settings.TRUSTED_PROXY_CIDRS or "").split(",")
             if c.strip()]
    if not cidrs:
        return peer
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    trusted = False
    for cidr in cidrs:
        try:
            if peer_ip in ipaddress.ip_network(cidr, strict=False):
                trusted = True
                break
        except ValueError:
            log.warning("TRUSTED_PROXY_CIDRS abaikan entri invalid: %r", cidr)
    if not trusted:
        return peer
    first = xff.split(",")[0].strip()
    try:
        ipaddress.ip_address(first)
    except ValueError:
        return peer
    return first


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    degraded: bool = False


class RateLimiter:
    """Penghitung per (scope, workspace, subject) dalam window berjalan."""

    def __init__(self, cache: CacheClient | None):
        self.cache = cache

    async def check(self, *, scope: str, workspace: str, subject: str,
                    limit: int, window_seconds: int = WINDOW_SECONDS,
                    now: int | None = None) -> RateDecision:
        if self.cache is None:
            return RateDecision(True, limit, limit, 0, degraded=True)
        window = int((now if now is not None else time.time()) // window_seconds)
        key = rate_limit_key(scope, workspace, subject, window)
        try:
            count, ttl = await self.cache.incr_window(key, window_seconds)
        except CacheUnavailable:
            log.warning("rate-limit fail-open (cache down) scope=%s workspace=%s",
                        scope, workspace)
            return RateDecision(True, limit, limit, 0, degraded=True)
        remaining = max(0, limit - count)
        if count > limit:
            return RateDecision(False, limit, 0, ttl if ttl > 0 else window_seconds)
        return RateDecision(True, limit, remaining, 0)


# --- dependency FastAPI (dipakai route terautentikasi) ---

def rate_limit_dependency(scope: str, limit_attr: str):
    """Dependency: batasi per (workspace, user) sesuai field Settings.

    Import dependency identitas di dalam factory (bukan top-level) untuk
    menghindari siklus modul; tetap memakai `Depends` agar override test
    (mis. identitas palsu) benar-benar berlaku.
    """
    from app.core.dependencies import get_cache, get_thread_identity

    async def _dep(
        request: Request,
        settings: Settings = Depends(get_settings),
        cache: CacheClient | None = Depends(get_cache),
        identity: ThreadIdentity = Depends(get_thread_identity),
    ) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        limit = int(getattr(settings, limit_attr))
        decision = await RateLimiter(cache).check(
            scope=scope, workspace=identity.workspace, subject=identity.user, limit=limit)
        if decision.degraded:
            request.state.rate_limit_degraded = True
        if not decision.allowed:
            log.warning("rate-limit ditolak scope=%s workspace=%s user=%s",
                        scope, identity.workspace, identity.user)
            raise RateLimitedError(decision.retry_after, decision.limit)

    return _dep


async def check_login_limit(request: Request, *, workspace: str, username: str,
                            settings: Settings, cache: CacheClient | None) -> None:
    """Login dibatasi per (IP + username) — anti credential stuffing."""
    if not settings.RATE_LIMIT_ENABLED:
        return
    ip = client_ip(request, settings)
    decision = await RateLimiter(cache).check(
        scope="login", workspace=workspace, subject=f"{ip}:{username}",
        limit=settings.RATE_LIMIT_LOGIN_PER_MIN)
    if decision.degraded:
        request.state.rate_limit_degraded = True
    if not decision.allowed:
        log.warning("rate-limit login ditolak workspace=%s user=%s ip=%s",
                    workspace, username, ip)
        raise RateLimitedError(decision.retry_after, decision.limit)


async def check_refresh_limit(request: Request, *, settings: Settings,
                              cache: CacheClient | None) -> None:
    """Refresh dibatasi per IP (v1.0 S8) — hentikan loop refresh satu sumber.

    Batasan sadar: penyerang terdistribusi tak terhentikan; pertahanan utama
    tetap rotasi atomik + TTL pendek (S5). Tanpa cache → fail-open + flag.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    ip = client_ip(request, settings)
    decision = await RateLimiter(cache).check(
        scope="refresh", workspace="-", subject=ip,
        limit=settings.RATE_LIMIT_REFRESH_PER_MIN)
    if decision.degraded:
        request.state.rate_limit_degraded = True
    if not decision.allowed:
        log.warning("rate-limit refresh ditolak ip=%s", ip)
        raise RateLimitedError(decision.retry_after, decision.limit)
