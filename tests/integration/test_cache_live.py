"""Integrasi: CacheClient terhadap Valkey ASLI (Lua, TTL, konkurensi)."""

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.integration


def _key(prefix: str = "test") -> str:
    return f"{prefix}:{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_ping_true(cache):
    assert await cache.ping() is True


@pytest.mark.asyncio
async def test_incr_window_counts_and_sets_ttl(cache):
    key = _key("rl")
    first = await cache.incr_window(key, 60)
    second = await cache.incr_window(key, 60)
    assert first[0] == 1 and second[0] == 2
    assert 0 < second[1] <= 60  # TTL terpasang oleh Lua
    await cache.delete(key)


@pytest.mark.asyncio
async def test_incr_window_atomic_under_concurrency(cache):
    """50 INCR paralel = tepat 50 (tidak ada lost update)."""
    key = _key("rl")
    results = await asyncio.gather(
        *[cache.incr_window(key, 60) for _ in range(50)])
    assert sorted(r[0] for r in results) == list(range(1, 51))
    await cache.delete(key)


@pytest.mark.asyncio
async def test_json_roundtrip_and_delete(cache):
    key = _key("idem")
    await cache.set_json(key, {"a": 1, "b": "x"}, ttl_seconds=60)
    assert await cache.get_json(key) == {"a": 1, "b": "x"}
    assert await cache.exists(key) is True
    await cache.delete(key)
    assert await cache.exists(key) is False


@pytest.mark.asyncio
async def test_set_if_absent_only_once(cache):
    key = _key("lock")
    assert await cache.set_if_absent(key, "owner-1", ttl_seconds=30) is True
    assert await cache.set_if_absent(key, "owner-2", ttl_seconds=30) is False
    await cache.delete(key)


@pytest.mark.asyncio
async def test_ttl_expiry(cache):
    key = _key("ttl")
    await cache.set_json(key, {"x": 1}, ttl_seconds=1)
    assert await cache.exists(key) is True
    await asyncio.sleep(1.3)
    assert await cache.exists(key) is False


@pytest.mark.asyncio
async def test_keys_are_namespaced(cache):
    """Key builder memakai prefix yang jelas (audit via KEYS mudah)."""
    from app.infra.cache import deny_key, idempotency_key, rate_limit_key

    assert rate_limit_key("chat", "t", "u", 1).startswith("rl:")
    assert idempotency_key("t", "u", "k").startswith("idem:")
    assert deny_key("j").startswith("jwt:deny:")
