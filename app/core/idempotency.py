"""Idempotency-Key untuk `POST /jobs/*` (v0.4).

Kenapa hanya jobs (bukan chat): chat sudah idempoten lewat `thread_id`
(retry = lanjutkan thread yang sama, bukan eksekusi baru). Jobs bersifat
"kerjakan sekali" sehingga butuh kunci eksplisit.

Alur (mengikuti semantik HTTP `Idempotency-Key`):
1. `begin()` → NEW: pemanggil mengeksekusi lalu `finish()` menyimpan respons.
2. Request ulang dengan kunci sama → REPLAY: respons tersimpan dikembalikan
   (tanpa eksekusi) + header `Idempotent-Replayed: true`.
3. Kunci sama tapi body berbeda → CONFLICT (409) — mencegah "kunci dipakai
   ulang untuk pekerjaan lain".
4. Request paralel dengan kunci sama → IN_PROGRESS (409) — klien retry nanti.

Fail-open: cache mati → NEW (eksekusi jalan, tanpa dedup) + log warning.

v1.0 R5 (lease, bukan pending-24-jam): state pending punya lease pendek
(IDEMPOTENCY_PENDING_LEASE_SECONDS, default 5 mnt) + token owner.
Proses mati di tengah → lease kedaluwarsa → pemegang kunci SAMA boleh
steal (ambil alih); hash terkunci 24 jam (payload beda = CONFLICT walau
lease habis — kunci tak boleh ganti makna). finish/abort compare-owner
atomik (tak bisa menimpa/menghapus milik pencuri). Semua operasi
atomik via Lua (FakeCache mengemulasi untuk unit test).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.degraded import report_degraded
from app.core.logging import get_logger
from app.infra.cache import CacheClient, CacheUnavailable, idempotency_key

log = get_logger(__name__)

STATE_PENDING = "pending"
STATE_DONE = "done"

NEW = "new"
REPLAY = "replay"
IN_PROGRESS = "in_progress"
CONFLICT = "conflict"

# begin atomik: kosong → pending; done+hash-sama → REPLAY; done+beda → CONFLICT;
# pending+hash-beda → CONFLICT; pending+lease-hidup → IN_PROGRESS;
# pending+lease-mati+hash-sama → STEAL (timpa dengan owner baru).
# ARGV: pending_json, ttl, payload_hash, now_epoch.
_BEGIN_LUA = """
local v = redis.call('GET', KEYS[1])
if not v then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 'NEW'
end
local d = cjson.decode(v)
if d['state'] == 'done' then
  if d['hash'] == ARGV[3] then return 'REPLAY:' .. v end
  return 'CONFLICT'
end
if d['hash'] ~= ARGV[3] then return 'CONFLICT' end
if tonumber(d['leased_until'] or '0') > tonumber(ARGV[4]) then
  return 'IN_PROGRESS'
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 'NEW'
"""

# finish: hanya pemilik yang boleh menandai done.
_FINISH_LUA = """
local v = redis.call('GET', KEYS[1])
if v then
  local d = cjson.decode(v)
  if d['owner'] ~= ARGV[3] then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
"""

# abort: hanya pemilik pending yang boleh menghapus.
_ABORT_LUA = """
local v = redis.call('GET', KEYS[1])
if v then
  local d = cjson.decode(v)
  if d['state'] == 'pending' and d['owner'] == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
  end
end
return 0
"""


@dataclass(frozen=True)
class IdemDecision:
    state: str
    response: dict[str, Any] | None = None
    degraded: bool = False  # True = fail-open (tanpa dedup) — route beri header
    owner: str = ""  # token pemilik (NEW saja) — wajib untuk finish/abort


def body_hash(payload: Any) -> str:
    """Hash stabil isi request (kunci dedup + deteksi payload berubah)."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IdempotencyGuard:
    def __init__(self, cache: CacheClient | None, ttl_seconds: int,
                 lease_seconds: int = 300):
        self.cache = cache
        self.ttl = max(60, int(ttl_seconds))
        self.lease = max(30, int(lease_seconds))

    async def begin(self, *, workspace: str, user: str, key: str,
                    payload_hash: str) -> IdemDecision:
        if self.cache is None:
            return IdemDecision(NEW)
        owner = uuid.uuid4().hex
        pending = json.dumps({"state": STATE_PENDING, "hash": payload_hash,
                              "owner": owner,
                              "leased_until": int(time.time()) + self.lease})
        try:
            raw = await self.cache.eval_int(
                _BEGIN_LUA, [idempotency_key(workspace, user, key)],
                [pending, self.ttl, payload_hash, int(time.time())])
        except CacheUnavailable:
            log.warning("idempotency fail-open (cache down) workspace=%s", workspace)
            report_degraded("idempotency", "cache_down")
            return IdemDecision(NEW, degraded=True)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if raw == "NEW":
            return IdemDecision(NEW, owner=owner)
        if raw == "IN_PROGRESS":
            return IdemDecision(IN_PROGRESS)
        if raw == "CONFLICT":
            return IdemDecision(CONFLICT)
        if isinstance(raw, str) and raw.startswith("REPLAY:"):
            try:
                stored = json.loads(raw[len("REPLAY:"):])
            except ValueError:
                return IdemDecision(NEW, degraded=True)
            if isinstance(stored.get("response"), dict):
                return IdemDecision(REPLAY, stored["response"])
            return IdemDecision(NEW, owner=owner)
        # Tak dikenal (fake tua?) → NEW aman tanpa owner.
        return IdemDecision(NEW)

    async def finish(self, *, workspace: str, user: str, key: str,
                     payload_hash: str, response: dict[str, Any],
                     owner: str = "") -> None:
        if self.cache is None:
            return
        done = json.dumps({"state": STATE_DONE, "hash": payload_hash,
                           "owner": owner, "response": response})
        try:
            await self.cache.eval_int(
                _FINISH_LUA, [idempotency_key(workspace, user, key)],
                [done, self.ttl, owner])
        except CacheUnavailable:
            log.warning("idempotency finish gagal (cache down) workspace=%s", workspace)
            report_degraded("idempotency", "finish_gagal")

    async def abort(self, *, workspace: str, user: str, key: str,
                    owner: str = "") -> None:
        """Lepas kunci saat eksekusi gagal, agar klien bisa retry."""
        if self.cache is None:
            return
        try:
            await self.cache.eval_int(
                _ABORT_LUA, [idempotency_key(workspace, user, key)], [owner])
        except CacheUnavailable:
            log.warning("idempotency abort gagal (cache down) workspace=%s", workspace)
            report_degraded("idempotency", "abort_gagal")
