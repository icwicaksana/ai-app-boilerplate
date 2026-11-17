"""Ketahanan tool subagent: retry transien + circuit breaker. MURNI (tanpa I/O).

PRINSIP: jangan pernah mengulang SELURUH subagent — tool tulis yang sudah
berhasil akan jalan lagi. Retry di level TOOL dengan backoff; breaker
mencegah anggaran token habis untuk API yang jelas mati.

Klasifikasi:
  transien  → TimeoutError/ConnectionError, status 429/502/503/504 → retry
  permanen  → sisanya → langsung gagal, JANGAN retry
  breaker   → 3 gagal/60 dtk → buka 5 mnt → short-circuit + lapor
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

TRANSIENT_STATUS = frozenset({429, 502, 503, 504})


def is_transient(exc: BaseException) -> bool:
    """True bila percobaan ulang masuk akal."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in TRANSIENT_STATUS:
        return True
    code = getattr(exc, "code", "")
    return isinstance(code, str) and code in {"429", "502", "503", "504"}


def error_class(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    return "transient" if is_transient(exc) else "permanent"


@dataclass
class CircuitBreaker:
    """Breaker per (subagent, tool). State hidup sepanjang proses."""

    failures: int = 0
    window_start: float = 0.0
    opened_until: float = 0.0
    max_failures: int = 3
    window_s: float = 60.0
    open_s: float = 300.0

    def is_open(self, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.opened_until

    def record_success(self, *, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self.failures = 0
        self.window_start = now
        self.opened_until = 0.0

    def record_failure(self, *, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        if now - self.window_start > self.window_s:
            self.failures = 0
            self.window_start = now
        self.failures += 1
        if self.failures >= self.max_failures:
            self.opened_until = now + self.open_s

    def remaining_open_s(self, *, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return max(0.0, self.opened_until - now)


class BreakerOpen(Exception):
    """Short-circuit: breaker terbuka, panggilan tidak dikirim."""


async def call_with_retry(
    func: Callable[[], Awaitable[Any]],
    *,
    breaker: CircuitBreaker | None = None,
    max_attempts: int = 3,
    base_delay_s: float = 0.5,
    attempt_timeout_s: float = 10.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> tuple[Any, int]:
    """Panggil `func` dengan retry transien. Kembalikan (hasil, n_attempts).

    - Breaker terbuka → langsung raise BreakerOpen (tanpa kirim).
    - Gagal permanen → raise segera (1 attempt tercatat).
    - Transien habis → raise exception terakhir.
    - `sleep` bisa di-inject (tes memakai no-op agar cepat).
    """
    if breaker is not None and breaker.is_open():
        raise BreakerOpen(
            f"breaker terbuka, coba lagi dalam "
            f"{breaker.remaining_open_s():.0f} dtk")
    do_sleep = sleep or asyncio.sleep
    last: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with asyncio.timeout(attempt_timeout_s):
                result = await func()
        except BreakerOpen:
            raise
        except Exception as exc:  # noqa: BLE001 - klasifikasi di bawah
            last = exc
            if not is_transient(exc):
                if breaker is not None:
                    breaker.record_failure()
                raise
            if attempt < max_attempts:
                delay = base_delay_s * (2 ** (attempt - 1))
                await do_sleep(delay + random.uniform(0, delay * 0.25))
                continue
            if breaker is not None:
                breaker.record_failure()
            raise
        else:
            if breaker is not None:
                breaker.record_success()
            return result, attempt
    assert last is not None  # tak terjangkau; untuk type-checker
    raise last
