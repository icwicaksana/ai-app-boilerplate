"""Unit: IdempotencyGuard (new/replay/conflict/in_progress/fail-open)."""

import pytest

from app.core.idempotency import (
    CONFLICT,
    IN_PROGRESS,
    NEW,
    REPLAY,
    IdempotencyGuard,
    body_hash,
)
from tests._fake_cache import FakeCache

TTL = 3600


def _guard(cache=None) -> IdempotencyGuard:
    return IdempotencyGuard(cache if cache is not None else FakeCache(), TTL)


async def _begin(guard, key="k1", payload=None):
    p = payload if payload is not None else {"items": [1]}
    return await guard.begin(tenant="toko-a", user="budi", key=key,
                             payload_hash=body_hash(p))


def test_body_hash_stable_and_order_independent():
    assert body_hash({"a": 1, "b": 2}) == body_hash({"b": 2, "a": 1})
    assert body_hash({"a": 1}) != body_hash({"a": 2})


@pytest.mark.asyncio
async def test_new_then_replay_after_finish():
    cache = FakeCache()
    guard = _guard(cache)
    assert (await _begin(guard)).state == NEW
    await guard.finish(tenant="toko-a", user="budi", key="k1",
                       payload_hash=body_hash({"items": [1]}),
                       response={"thread_id": "t", "status": "completed"})
    replay = await _begin(guard)
    assert replay.state == REPLAY
    assert replay.response == {"thread_id": "t", "status": "completed"}


@pytest.mark.asyncio
async def test_pending_is_in_progress():
    guard = _guard()
    await _begin(guard)
    assert (await _begin(guard)).state == IN_PROGRESS


@pytest.mark.asyncio
async def test_same_key_different_payload_conflicts():
    guard = _guard()
    await _begin(guard, payload={"items": [1]})
    assert (await _begin(guard, payload={"items": [2]})).state == CONFLICT


@pytest.mark.asyncio
async def test_abort_allows_retry():
    guard = _guard()
    await _begin(guard)
    await guard.abort(tenant="toko-a", user="budi", key="k1")
    assert (await _begin(guard)).state == NEW


@pytest.mark.asyncio
async def test_different_keys_independent():
    guard = _guard()
    assert (await _begin(guard, key="k1")).state == NEW
    assert (await _begin(guard, key="k2")).state == NEW


@pytest.mark.asyncio
async def test_tenant_isolation():
    guard = _guard()
    await _begin(guard, key="same")
    other = await guard.begin(tenant="toko-b", user="budi", key="same",
                              payload_hash=body_hash({"items": [1]}))
    assert other.state == NEW


@pytest.mark.asyncio
async def test_fail_open_when_cache_down():
    cache = FakeCache()
    cache.down = True
    guard = _guard(cache)
    assert (await _begin(guard)).state == NEW
    # finish/abort tidak boleh raise
    await guard.finish(tenant="toko-a", user="budi", key="k1",
                       payload_hash="h", response={})
    await guard.abort(tenant="toko-a", user="budi", key="k1")


@pytest.mark.asyncio
async def test_no_cache_always_new():
    guard = IdempotencyGuard(None, TTL)
    assert (await _begin(guard)).state == NEW
