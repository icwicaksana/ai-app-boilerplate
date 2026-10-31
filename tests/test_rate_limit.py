"""Unit: rate limiter fixed-window (logika + fail-open + isolasi identitas)."""

import pytest

from app.core.ratelimit import RateLimiter
from tests._fake_cache import FakeCache


async def _check(limiter, limit=3, subject="budi", tenant="toko-a",
                 scope="chat", now=1000.0):
    return await limiter.check(scope=scope, tenant=tenant, subject=subject,
                               limit=limit, now=now)


@pytest.mark.asyncio
async def test_allows_up_to_limit_then_blocks():
    lim = RateLimiter(FakeCache())
    results = [await _check(lim) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].remaining == 0
    assert results[-1].retry_after > 0


@pytest.mark.asyncio
async def test_window_reset_allows_again():
    lim = RateLimiter(FakeCache())
    for _ in range(3):
        await _check(lim)
    assert (await _check(lim, now=1000.0)).allowed is False
    # window berikutnya (60s) = kunci baru
    assert (await _check(lim, now=1060.0)).allowed is True


@pytest.mark.asyncio
async def test_identity_isolation():
    cache = FakeCache()
    lim = RateLimiter(cache)
    for _ in range(3):
        await _check(lim, subject="budi")
    assert (await _check(lim, subject="budi")).allowed is False
    # user lain di tenant sama tidak terpengaruh
    assert (await _check(lim, subject="ani")).allowed is True
    # tenant lain juga tidak
    assert (await _check(lim, tenant="toko-b", subject="budi")).allowed is True


@pytest.mark.asyncio
async def test_scope_isolation():
    cache = FakeCache()
    lim = RateLimiter(cache)
    for _ in range(3):
        await _check(lim, scope="chat")
    assert (await _check(lim, scope="chat")).allowed is False
    assert (await _check(lim, scope="jobs")).allowed is True


@pytest.mark.asyncio
async def test_fail_open_when_cache_down():
    cache = FakeCache()
    cache.down = True
    decision = await _check(RateLimiter(cache))
    assert decision.allowed is True and decision.degraded is True


@pytest.mark.asyncio
async def test_fail_open_when_cache_absent():
    decision = await _check(RateLimiter(None))
    assert decision.allowed is True and decision.degraded is True


@pytest.mark.asyncio
async def test_remaining_counts_down():
    lim = RateLimiter(FakeCache())
    first = await _check(lim, limit=5)
    second = await _check(lim, limit=5)
    assert (first.remaining, second.remaining) == (4, 3)
