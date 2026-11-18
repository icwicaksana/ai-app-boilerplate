"""Turn log resumable di Valkey Streams (v0.5a, ADR-20).

Kenapa stream, bukan buffer proses: resume harus tetap bekerja walau klien
masuk ke pod lain, atau proses producer sudah selesai/berpindah. Satu turn =
satu key stream; entry ID Redis menjadi **satu-satunya cursor** yang dipakai
SSE `id:` dan `Last-Event-ID`.

Kontrak:
- `create()` menulis event pertama + TTL **atomik** (Lua) — kalau proses mati
  tepat setelah XADD, key tetap punya TTL (pelajaran Glance).
- Event terminal (`result`/`error`) menandai akhir; `finish()` memperpendek
  TTL ke jendela reconnect.
- Turn kedaluwarsa → `exists()` False → route membalas 410 (bukan menggantung).
- Key hanya `workspace + turn_id` (thread_id ada di payload `started`), sehingga
  resume tak perlu tahu thread lebih dulu.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.infra.cache import CacheClient, CacheUnavailable

log = get_logger(__name__)

TERMINAL_TYPES = frozenset({"result", "error"})
MAX_EVENTS_PER_TURN = 20_000  # pagar memori per turn
DEFAULT_TTL_SECONDS = 900     # selama turn berjalan
RECONNECT_TTL_SECONDS = 300   # jendela reconnect setelah selesai

# XADD + EXPIRE + owner dalam satu langkah atomik.
_CREATE_LUA = """
local id = redis.call('XADD', KEYS[1], '*', 'data', ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[2])
return id
"""


def turn_key(workspace: str, turn_id: str) -> str:
    return f"turn:{workspace}:{turn_id}"


def turn_owner_key(workspace: str, turn_id: str) -> str:
    """Kunci pemilik turn (v1.0 S3): resume lintas-user ditolak."""
    return f"turn:{workspace}:{turn_id}:owner"


def new_turn_id() -> str:
    return uuid.uuid4().hex[:16]


def is_terminal(event: dict[str, Any]) -> bool:
    return str(event.get("type", "")) in TERMINAL_TYPES


class TurnLog:
    """Penulis/pembaca event stream untuk satu turn."""

    def __init__(self, cache: CacheClient | None):
        self.cache = cache

    @property
    def enabled(self) -> bool:
        return self.cache is not None

    async def create(self, *, workspace: str, turn_id: str, owner: str,
                     first_event: dict[str, Any],
                     ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
        """Buat stream + event pertama + owner (atomik dengan TTL).

        `owner` WAJIB (fail-closed): resume memverifikasi pemilik sebelum
        memompa event. Return entry ID.
        """
        if self.cache is None:
            return "0-0"
        payload = json.dumps(first_event, ensure_ascii=False, default=str)
        try:
            entry_id = await self.cache.eval_int(
                _CREATE_LUA, [turn_key(workspace, turn_id),
                              turn_owner_key(workspace, turn_id)],
                [payload, int(ttl_seconds), owner])
            return str(entry_id)
        except CacheUnavailable as exc:
            # Fallback sekuensial: fake/Redis purba tanpa EVAL (unit test
            # memakai fakeredis yang tak mengimplementasikan eval). Valkey
            # asli selalu lewat jalur Lua atomik di atas. Cache yang
            # benar-benar mati tetap raise dari bawah (fail-closed create).
            log.debug("turn create tanpa Lua (%s) — fallback sekuensial",
                      type(exc).__name__)
            entry_id = await self.cache.xadd(
                turn_key(workspace, turn_id), {"data": payload})
            await self.cache.expire(turn_key(workspace, turn_id),
                                    int(ttl_seconds))
            await self.cache.set_str(turn_owner_key(workspace, turn_id),
                                     owner, int(ttl_seconds))
            return str(entry_id)

    async def owner_of(self, *, workspace: str, turn_id: str) -> str | None:
        """Pemilik turn, atau None bila turn/owner tak ada (diperlakukan 410)."""
        if self.cache is None:
            return None
        return await self.cache.get_str(turn_owner_key(workspace, turn_id))

    async def append(self, *, workspace: str, turn_id: str,
                     event: dict[str, Any]) -> str:
        if self.cache is None:
            return "0-0"
        return await self.cache.xadd(
            turn_key(workspace, turn_id),
            {"data": json.dumps(event, ensure_ascii=False, default=str)},
            maxlen=MAX_EVENTS_PER_TURN)

    async def finish(self, *, workspace: str, turn_id: str,
                     ttl_seconds: int = RECONNECT_TTL_SECONDS) -> None:
        """Turn selesai: perpendek TTL ke jendela reconnect."""
        if self.cache is None:
            return
        try:
            await self.cache.expire(turn_key(workspace, turn_id), ttl_seconds)
            # P2: key owner ikut diperpendek — kalau tidak, owner bertahan
            # dengan TTL asli (900s) setelah stream kedaluwarsa (300s).
            await self.cache.expire(turn_owner_key(workspace, turn_id),
                                    ttl_seconds)
        except CacheUnavailable:
            log.warning("turnlog finish gagal (cache down) turn=%s", turn_id)

    async def exists(self, *, workspace: str, turn_id: str) -> bool:
        if self.cache is None:
            return False
        try:
            return await self.cache.exists(turn_key(workspace, turn_id))
        except CacheUnavailable:
            return False

    async def replay(self, *, workspace: str, turn_id: str,
                     cursor: str = "0-0") -> list[tuple[str, dict[str, Any]]]:
        """Semua event setelah cursor (untuk resume/inspeksi)."""
        if self.cache is None:
            return []
        rows = await self.cache.xrange(turn_key(workspace, turn_id),
                                       start=_after(cursor))
        return [(rid, _decode(fields)) for rid, fields in rows]

    async def follow(self, *, workspace: str, turn_id: str, cursor: str = "0-0",
                     block_ms: int = 1000,
                     idle_timeout_s: float = 60.0) -> AsyncIterator[tuple[str, dict]]:
        """Ikuti turn dari cursor: replay lalu live sampai event terminal.

        Berhenti saat event terminal, saat `idle_timeout_s` tanpa event baru,
        atau saat key hilang (kedaluwarsa).
        """
        if self.cache is None:
            return
        pos = _xread_start(cursor)
        idle = 0.0
        while idle < idle_timeout_s:
            rows = await self.cache.xread(turn_key(workspace, turn_id), pos,
                                          block_ms=block_ms)
            if not rows:
                idle += block_ms / 1000.0
                if not await self.exists(workspace=workspace, turn_id=turn_id):
                    return
                continue
            idle = 0.0
            for rid, fields in rows:
                event = _decode(fields)
                pos = rid
                yield rid, event
                if is_terminal(event):
                    return


def _after(cursor: str) -> str:
    """Redis XRANGE inklusif; cursor kita eksklusif → naikkan sequence."""
    if not cursor or cursor in ("0", "0-0", "-"):
        return "-"
    try:
        ms, seq = cursor.split("-", 1)
        return f"{ms}-{int(seq) + 1}"
    except (ValueError, AttributeError):
        return "-"


def _xread_start(cursor: str) -> str:
    """XREAD tidak menerima '-' (beda dengan XRANGE); mulai dari '0-0'."""
    pos = _after(cursor)
    return "0-0" if pos == "-" else pos


def _decode(fields: dict[str, Any]) -> dict[str, Any]:
    raw = fields.get("data") if isinstance(fields, dict) else None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return {"type": "unknown"}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"type": "unknown"}
    return parsed if isinstance(parsed, dict) else {"type": "unknown"}
