"""Ticket auth untuk WebSocket (v0.5b, ADR-21).

Kenapa ticket, bukan token di query: browser `WebSocket()` tidak bisa
mengirim header `Authorization`, dan menaruh access token di URL membuatnya
tercatat di log/proxy. Ticket = token sekali pakai, TTL pendek, terikat ke
identitas (dan opsional thread), ditukar lewat endpoint HTTP terautentikasi.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from app.core.logging import get_logger
from app.infra.cache import CacheClient, CacheUnavailable

log = get_logger(__name__)

TICKET_PREFIX = "wsticket"

# GET + DEL atomik: ticket hanya bisa dipakai SEKALI walau ada dua koneksi
# yang mencoba bersamaan.
_CONSUME_LUA = """
local v = redis.call('GET', KEYS[1])
if v then redis.call('DEL', KEYS[1]) end
return v
"""


def ticket_key(ticket: str) -> str:
    return f"{TICKET_PREFIX}:{ticket}"


@dataclass(frozen=True)
class WsIdentity:
    workspace: str
    user: str
    thread_id: str | None = None
    # v1.0 S6: scope diikat ke ticket (gate chat == HTTP) + jti untuk cek
    # revoke berkala (logout di tengah koneksi memutus WS).
    scopes: tuple[str, ...] = ()
    jti: str | None = None


class TicketStore:
    def __init__(self, cache: CacheClient | None, ttl_seconds: int = 30):
        self.cache = cache
        self.ttl = ttl_seconds

    async def issue(self, *, workspace: str, user: str,
                    thread_id: str | None = None,
                    scopes: tuple[str, ...] = (),
                    jti: str | None = None) -> tuple[str, int]:
        """Terbitkan ticket baru. Raise CacheUnavailable bila cache mati."""
        if self.cache is None:
            raise CacheUnavailable("cache nonaktif — WebSocket tidak tersedia")
        ticket = secrets.token_urlsafe(32)
        await self.cache.set_json(
            ticket_key(ticket),
            {"workspace": workspace, "user": user, "thread_id": thread_id,
             "scopes": list(scopes), "jti": jti},
            ttl_seconds=self.ttl)
        return ticket, self.ttl

    async def consume(self, ticket: str) -> WsIdentity | None:
        """Tukar ticket → identitas. None bila tidak valid/kedaluwarsa/palsu."""
        if not ticket or self.cache is None:
            return None
        try:
            raw = await self.cache.eval_int(_CONSUME_LUA, [ticket_key(ticket)], [])
        except CacheUnavailable:
            return None
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            return None
        if not isinstance(data, dict) or not data.get("workspace") or not data.get("user"):
            return None
        raw_scopes = data.get("scopes") or []
        scopes = tuple(str(s) for s in raw_scopes) if isinstance(raw_scopes, list) else ()
        return WsIdentity(workspace=str(data["workspace"]), user=str(data["user"]),
                          thread_id=(str(data["thread_id"])
                                     if data.get("thread_id") else None),
                          scopes=scopes,
                          jti=(str(data["jti"]) if data.get("jti") else None))


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    """Validasi Origin handshake (CWE-1385 / OWASP ASVS 4.4).

    - Origin ada di allowlist → boleh.
    - Origin tidak ada → boleh (klien non-browser; browser selalu mengirim).
    - Selain itu → tolak.
    """
    if not origin:
        return True
    normalized = origin.strip().rstrip("/").lower()
    return normalized in {o.lower() for o in allowed}
