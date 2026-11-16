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
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.infra.cache import CacheClient, CacheUnavailable, idempotency_key

log = get_logger(__name__)

STATE_PENDING = "pending"
STATE_DONE = "done"

NEW = "new"
REPLAY = "replay"
IN_PROGRESS = "in_progress"
CONFLICT = "conflict"


@dataclass(frozen=True)
class IdemDecision:
    state: str
    response: dict[str, Any] | None = None


def body_hash(payload: Any) -> str:
    """Hash stabil isi request (kunci dedup + deteksi payload berubah)."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IdempotencyGuard:
    def __init__(self, cache: CacheClient | None, ttl_seconds: int):
        self.cache = cache
        self.ttl = max(60, int(ttl_seconds))

    async def begin(self, *, workspace: str, user: str, key: str,
                    payload_hash: str) -> IdemDecision:
        if self.cache is None:
            return IdemDecision(NEW)
        cache_key = idempotency_key(workspace, user, key)
        pending = json.dumps({"state": STATE_PENDING, "hash": payload_hash})
        try:
            if await self.cache.set_if_absent(cache_key, pending, self.ttl):
                return IdemDecision(NEW)  # kita pemilik eksekusi
            stored = await self.cache.get_json(cache_key)
        except CacheUnavailable:
            log.warning("idempotency fail-open (cache down) workspace=%s", workspace)
            return IdemDecision(NEW)
        if not stored:
            # Kunci hilang di antara NX dan GET (TTL habis) → anggap baru.
            return IdemDecision(NEW)
        if stored.get("hash") != payload_hash:
            return IdemDecision(CONFLICT)
        if stored.get("state") == STATE_DONE and isinstance(stored.get("response"), dict):
            return IdemDecision(REPLAY, stored["response"])
        return IdemDecision(IN_PROGRESS)

    async def finish(self, *, workspace: str, user: str, key: str,
                     payload_hash: str, response: dict[str, Any]) -> None:
        if self.cache is None:
            return
        try:
            await self.cache.set_json(
                idempotency_key(workspace, user, key),
                {"state": STATE_DONE, "hash": payload_hash, "response": response},
                ttl_seconds=self.ttl,
            )
        except CacheUnavailable:
            log.warning("idempotency finish gagal (cache down) workspace=%s", workspace)

    async def abort(self, *, workspace: str, user: str, key: str) -> None:
        """Lepas kunci saat eksekusi gagal, agar klien bisa retry."""
        if self.cache is None:
            return
        try:
            await self.cache.delete(idempotency_key(workspace, user, key))
        except CacheUnavailable:
            log.warning("idempotency abort gagal (cache down) workspace=%s", workspace)
