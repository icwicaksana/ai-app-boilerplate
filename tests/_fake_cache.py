"""Fake cache untuk unit test: meniru kontrak CacheClient tanpa Redis.

Kenapa bukan fakeredis: `incr_window` memakai Lua (`EVAL`) yang tidak
didukung fakeredis. Kontrak di sini meniru perilaku Valkey (counter + TTL)
sehingga logika rate-limit/idempotency bisa diuji deterministik.
"""

from __future__ import annotations

import json
from typing import Any


class FakeCache:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.store: dict[str, str] = {}
        self.streams: dict[str, list[tuple[str, str]]] = {}
        self.down = False
        self.incr_calls = 0

    # --- kontrak CacheClient ---
    async def ping(self) -> bool:
        return not self.down

    async def incr_window(self, key: str, ttl_seconds: int) -> tuple[int, int]:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        self.incr_calls += 1
        self.counters[key] = self.counters.get(key, 0) + 1
        self.ttls.setdefault(key, int(ttl_seconds))
        return self.counters[key], self.ttls[key]

    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        if key in self.store:
            return False
        self.store[key] = value
        self.ttls[key] = int(ttl_seconds)
        return True

    async def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        self.store[key] = json.dumps(value)
        self.ttls[key] = int(ttl_seconds)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        raw = self.store.get(key)
        return json.loads(raw) if raw else None

    async def exists(self, key: str) -> bool:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        return key in self.store

    async def delete(self, key: str) -> None:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        self.store.pop(key, None)

    async def close(self) -> None:
        return None

    # --- v0.5a/v0.5b: emulasi Lua yang dipakai produksi ---
    async def eval_int(self, script: str, keys: list[str], args: list[Any]) -> Any:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        key = keys[0]
        if "XADD" in script:
            self.streams.setdefault(key, []).append((f"{len(self.streams[key]) + 1}-0",
                                                     str(args[0])))
            self.ttls[key] = int(args[1])
            return f"{len(self.streams[key])}-0"
        if "DEL" in script:  # consume ticket (GETDEL)
            return self.store.pop(key, None)
        raise NotImplementedError("script tak dikenal di FakeCache")

    async def xadd(self, key: str, fields: dict[str, Any],
                   maxlen: int | None = None) -> str:
        if self.down:
            from app.infra.cache import CacheUnavailable

            raise CacheUnavailable("fake down")
        rows = self.streams.setdefault(key, [])
        rid = f"{len(rows) + 1}-0"
        rows.append((rid, str(fields.get("data", ""))))
        return rid

    async def xrange(self, key: str, start: str = "-", end: str = "+",
                     count: int = 500) -> list[tuple[str, dict]]:
        rows = self.streams.get(key, [])
        out = [(rid, {"data": data}) for rid, data in rows]
        if start not in ("-", "0-0"):
            out = [(rid, f) for rid, f in out if rid >= start]
        return out[:count]

    async def xread(self, key: str, cursor: str, block_ms: int = 1000,
                    count: int = 200) -> list[tuple[str, dict]]:
        rows = [(rid, {"data": data}) for rid, data in self.streams.get(key, [])]
        if cursor not in ("0-0", "0"):
            rows = [(rid, f) for rid, f in rows if rid > cursor]
        return rows[:count]

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        if key not in self.store and key not in self.streams:
            return False
        self.ttls[key] = int(ttl_seconds)
        return True
