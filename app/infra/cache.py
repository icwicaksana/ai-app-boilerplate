"""Cache Valkey/Redis (v0.4, ADR-13): rate-limit, idempotency, denylist JWT.

Prinsip:
- **Satu pool** dibuat di lifespan (pola sama seperti Postgres) — jangan
  koneksi per request.
- **Tanpa fallback in-memory** (ADR-15): `CACHE_URL` kosong = fitur nonaktif
  dengan warning jelas, bukan jalur kedua yang menyembunyikan salah config.
- **Fail-soft**: semua operasi membungkus error menjadi `CacheUnavailable`.
  Pemanggil yang memutuskan sikapnya (rate-limit fail-open, auth fail-open
  dengan log, health melaporkan `down`) — cache tak boleh mematikan BE.
- Perintah yang dipakai HANYA perintah inti (INCR/EXPIRE/TTL/SET/GET/DEL) —
  tidak bergantung pada modul JSON/Search, walau image Bundle menyediakannya
  untuk kebutuhan nanti (v0.7 RAG).

Namespace key (satu Redis, prefix per fungsi — mudah diaudit via `KEYS`):
- `rl:{scope}:{workspace}:{user}:{window}`  — counter rate-limit (TTL = window)
- `idem:{workspace}:{user}:{key}`           — idempotency (TTL = 24 jam)
- `jwt:deny:{jti}`                       — token yang di-revoke (TTL = sisa umur)
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

PREFIX_RL = "rl"
PREFIX_IDEM = "idem"
PREFIX_DENY = "jwt:deny"

# INCR + EXPIRE + TTL dalam satu round-trip ATOMIK (cegah counter tanpa TTL
# bila proses mati di antara dua perintah).
_INCR_WINDOW_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {c, redis.call('TTL', KEYS[1])}
"""


class CacheUnavailable(RuntimeError):
    """Cache tak bisa dipakai (down/timeout). Pemanggil putuskan fail-open."""


# ---------------------------------------------------------------------------
# Key builders — satu sumber, dipakai route/limiter/guard/tes.
# ---------------------------------------------------------------------------

def rate_limit_key(scope: str, workspace: str, user: str, window: int) -> str:
    return f"{PREFIX_RL}:{scope}:{workspace}:{user}:{window}"


def idempotency_key(workspace: str, user: str, key: str) -> str:
    return f"{PREFIX_IDEM}:{workspace}:{user}:{key}"


def deny_key(jti: str) -> str:
    return f"{PREFIX_DENY}:{jti}"


class CacheClient:
    """Wrapper tipis di atas `redis.asyncio` dengan error seragam."""

    def __init__(self, client: aioredis.Redis):
        self._client = client

    @classmethod
    def from_url(cls, url: str, socket_timeout: float) -> "CacheClient":
        client = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            health_check_interval=30,
        )
        return cls(client)

    # --- lifecycle ---
    async def open(self) -> None:
        """Verifikasi koneksi saat startup (fail-fast di lifespan)."""
        await self.ping()

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            log.warning("cache close gagal (diabaikan agar shutdown bersih)",
                        exc_info=True)

    async def ping(self) -> bool:
        """True bila PONG. Tak pernah raise (dipakai health/startup)."""
        try:
            return bool(await self._client.ping())
        except Exception as exc:
            log.warning("cache ping gagal: %s: %s", type(exc).__name__, exc)
            return False

    # --- operasi dasar (semua raise CacheUnavailable bila gagal) ---
    async def incr_window(self, key: str, ttl_seconds: int) -> tuple[int, int]:
        """Naikkan counter dalam window; kembalikan (count, ttl_sisa)."""
        try:
            count, ttl = await self._client.eval(
                _INCR_WINDOW_LUA, 1, key, int(ttl_seconds))
            return int(count), int(ttl)
        except Exception as exc:
            raise CacheUnavailable(f"incr_window gagal: {exc}") from exc

    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        """SET NX EX → True bila kunci baru dibuat (pemilik lock)."""
        try:
            res = await self._client.set(
                key, value, nx=True, ex=max(1, int(ttl_seconds)))
            return bool(res)
        except Exception as exc:
            raise CacheUnavailable(f"set_if_absent gagal: {exc}") from exc

    async def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        try:
            await self._client.set(
                key, json.dumps(value, ensure_ascii=False),
                ex=max(1, int(ttl_seconds)))
        except Exception as exc:
            raise CacheUnavailable(f"set_json gagal: {exc}") from exc

    async def get_str(self, key: str) -> str | None:
        """Baca string mentah (mis. owner turn). None bila tak ada."""
        try:
            raw = await self._client.get(key)
        except Exception as exc:
            raise CacheUnavailable(f"get_str gagal: {exc}") from exc
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def set_str(self, key: str, value: str, ttl_seconds: int) -> None:
        """Tulis string mentah dengan TTL (mis. owner turn)."""
        try:
            await self._client.set(key, value, ex=max(1, int(ttl_seconds)))
        except Exception as exc:
            raise CacheUnavailable(f"set_str gagal: {exc}") from exc

    async def get_json(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._client.get(key)
        except Exception as exc:
            raise CacheUnavailable(f"get_json gagal: {exc}") from exc
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("cache nilai bukan JSON valid (key=%s) — diabaikan", key)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._client.exists(key))
        except Exception as exc:
            raise CacheUnavailable(f"exists gagal: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception as exc:
            raise CacheUnavailable(f"delete gagal: {exc}") from exc

    # --- stream ops (v0.5a: turn log resumable) ---

    async def xadd(self, key: str, fields: dict[str, Any],
                   maxlen: int | None = None) -> str:
        """Tambah entri stream; kembalikan entry ID (cursor untuk resume)."""
        try:
            return str(await self._client.xadd(
                key, fields, maxlen=maxlen, approximate=True))
        except Exception as exc:
            raise CacheUnavailable(f"xadd gagal: {exc}") from exc

    async def xrange(self, key: str, start: str = "-",
                     end: str = "+", count: int = 500) -> list[tuple[str, dict]]:
        """Baca entri dari cursor `start` (inklusif)."""
        try:
            rows = await self._client.xrange(key, min=start, max=end, count=count)
        except Exception as exc:
            raise CacheUnavailable(f"xrange gagal: {exc}") from exc
        return [(str(rid), dict(fields)) for rid, fields in rows or []]

    async def xread(self, key: str, cursor: str, block_ms: int = 1000,
                    count: int = 200) -> list[tuple[str, dict]]:
        """Baca entri BARU setelah `cursor` (blocking singkat)."""
        try:
            res = await self._client.xread({key: cursor}, block=block_ms, count=count)
        except Exception as exc:
            raise CacheUnavailable(f"xread gagal: {exc}") from exc
        out: list[tuple[str, dict]] = []
        for _stream, entries in res or []:
            for rid, fields in entries:
                out.append((str(rid), dict(fields)))
        return out

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        try:
            return bool(await self._client.expire(key, max(1, int(ttl_seconds))))
        except Exception as exc:
            raise CacheUnavailable(f"expire gagal: {exc}") from exc

    async def eval_int(self, script: str, keys: list[str], args: list[Any]) -> Any:
        """Jalankan Lua yang mengembalikan nilai sederhana."""
        try:
            return await self._client.eval(script, len(keys), *keys, *args)
        except Exception as exc:
            raise CacheUnavailable(f"eval gagal: {exc}") from exc


def build_cache(settings: Settings) -> CacheClient | None:
    """Cache dari settings; None bila CACHE_URL kosong (fitur nonaktif)."""
    url = (settings.CACHE_URL or "").strip()
    if not url:
        log.warning("cache NONAKTIF (CACHE_URL kosong) — rate-limit/idempotency/"
                    "denylist JWT tidak berjalan. Isi CACHE_URL untuk produksi.")
        return None
    return CacheClient.from_url(url, settings.CACHE_SOCKET_TIMEOUT)
