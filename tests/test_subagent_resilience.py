"""v0.9 F4: retry transien + circuit breaker (tanpa LLM, tanpa jaringan)."""

from __future__ import annotations

import pytest

from app.agents.subagents.resilience import (
    BreakerOpen,
    CircuitBreaker,
    call_with_retry,
    error_class,
    is_transient,
)


class _Status(Exception):
    def __init__(self, status: int):
        super().__init__(f"http {status}")
        self.status_code = status


def test_klasifikasi_transien():
    assert is_transient(TimeoutError())
    assert is_transient(ConnectionError())
    assert is_transient(_Status(429))
    assert is_transient(_Status(503))
    assert not is_transient(_Status(400))
    assert not is_transient(ValueError("salah argumen"))
    assert error_class(TimeoutError()) == "timeout"
    assert error_class(_Status(503)) == "transient"
    assert error_class(ValueError()) == "permanent"


@pytest.mark.asyncio
async def test_retry_berhasil_setelah_gagal():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("putus")
        return "ok"

    async def no_sleep(_):
        return None

    result, attempts = await call_with_retry(flaky, sleep=no_sleep)
    assert (result, attempts) == ("ok", 3)


@pytest.mark.asyncio
async def test_retry_permanen_langsung_gagal():
    async def bad():
        raise ValueError("argumen salah")

    async def no_sleep(_):
        raise AssertionError("tak boleh tidur untuk permanen")

    with pytest.raises(ValueError):
        await call_with_retry(bad, sleep=no_sleep)


@pytest.mark.asyncio
async def test_retry_transien_habis_melempar_terakhir():
    async def down():
        raise _Status(503)

    async def no_sleep(_):
        return None

    with pytest.raises(_Status):
        await call_with_retry(down, max_attempts=2, sleep=no_sleep)


@pytest.mark.asyncio
async def test_breaker_terbuka_setelah_3_gagal():
    b = CircuitBreaker(max_failures=3, window_s=60, open_s=300)

    async def down():
        raise ConnectionError()

    async def no_sleep(_):
        return None

    for _ in range(3):
        with pytest.raises(ConnectionError):
            await call_with_retry(down, breaker=b, max_attempts=1,
                                  sleep=no_sleep)
    assert b.is_open()
    with pytest.raises(BreakerOpen):
        await call_with_retry(down, breaker=b, sleep=no_sleep)


@pytest.mark.asyncio
async def test_breaker_sukses_menutup():
    b = CircuitBreaker(max_failures=1, window_s=60, open_s=300)

    async def down():
        raise ConnectionError()

    async def up():
        return "ok"

    async def no_sleep(_):
        return None

    with pytest.raises(ConnectionError):
        await call_with_retry(down, breaker=b, max_attempts=1, sleep=no_sleep)
    assert b.is_open()
    # Tunggu buka habis secara logis (bukan tidur 5 menit).
    b.opened_until = 0.0
    result, _ = await call_with_retry(up, breaker=b, sleep=no_sleep)
    assert result == "ok"
    assert not b.is_open()


@pytest.mark.asyncio
async def test_attempt_timeout_terhitung_transien():
    import asyncio as _aio

    async def lambat():
        await _aio.sleep(5)
        return "x"

    async def no_sleep(_):
        return None

    with pytest.raises(TimeoutError):
        await call_with_retry(lambat, max_attempts=1, attempt_timeout_s=0.05,
                              sleep=no_sleep)
