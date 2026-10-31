"""Integrasi: rate-limit + idempotency di atas Valkey ASLI (bukan fake)."""

import asyncio
import uuid

import pytest

from app.core.idempotency import CONFLICT, IN_PROGRESS, NEW, REPLAY, IdempotencyGuard, body_hash
from app.core.ratelimit import RateLimiter

pytestmark = pytest.mark.integration


def _subject() -> str:
    return f"u-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_rate_limit_exact_limit_under_concurrency(cache):
    """50 request paralel, limit 10 → tepat 10 lolos (atomik)."""
    lim = RateLimiter(cache)
    subject = _subject()
    decisions = await asyncio.gather(*[
        lim.check(scope="chat", tenant="toko-a", subject=subject, limit=10)
        for _ in range(50)
    ])
    allowed = [d for d in decisions if d.allowed]
    assert len(allowed) == 10
    assert all(d.remaining >= 0 for d in allowed)


@pytest.mark.asyncio
async def test_rate_limit_isolated_tenants_live(cache):
    lim = RateLimiter(cache)
    subject = _subject()
    for _ in range(3):
        await lim.check(scope="jobs", tenant="toko-a", subject=subject, limit=3)
    assert (await lim.check(scope="jobs", tenant="toko-a",
                            subject=subject, limit=3)).allowed is False
    assert (await lim.check(scope="jobs", tenant="toko-b",
                            subject=subject, limit=3)).allowed is True


@pytest.mark.asyncio
async def test_idempotency_flow_live(cache):
    guard = IdempotencyGuard(cache, ttl_seconds=60)
    key = uuid.uuid4().hex[:12]
    h = body_hash({"n": 1})
    assert (await guard.begin(tenant="toko-a", user="budi", key=key,
                              payload_hash=h)).state == NEW
    assert (await guard.begin(tenant="toko-a", user="budi", key=key,
                              payload_hash=h)).state == IN_PROGRESS
    assert (await guard.begin(tenant="toko-a", user="budi", key=key,
                              payload_hash=body_hash({"n": 2}))).state == CONFLICT
    await guard.finish(tenant="toko-a", user="budi", key=key, payload_hash=h,
                       response={"thread_id": "t", "status": "completed"})
    replay = await guard.begin(tenant="toko-a", user="budi", key=key, payload_hash=h)
    assert replay.state == REPLAY
    assert replay.response["status"] == "completed"


@pytest.mark.asyncio
async def test_idempotency_only_one_winner_concurrent(cache):
    guard = IdempotencyGuard(cache, ttl_seconds=60)
    key = uuid.uuid4().hex[:12]
    h = body_hash({"n": 1})
    states = await asyncio.gather(*[
        guard.begin(tenant="toko-a", user="budi", key=key, payload_hash=h)
        for _ in range(20)
    ])
    assert sum(1 for s in states if s.state == NEW) == 1
    assert sum(1 for s in states if s.state == IN_PROGRESS) == 19
