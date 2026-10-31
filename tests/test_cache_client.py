"""Unit: CacheClient + key builders (fakeredis, tanpa Valkey).

Lua (`incr_window`) TIDAK didukung fakeredis — perilaku itu diuji di
integrasi terhadap Valkey asli (`tests/integration/test_cache_live.py`).
Di sini yang dikunci: kontrak key, round-trip JSON, fail-soft, dan
CacheUnavailable saat backend tak mendukung perintah.
"""

import pytest
from fakeredis.aioredis import FakeRedis

from app.core.config import Settings
from app.infra.cache import (
    CacheClient,
    CacheUnavailable,
    build_cache,
    deny_key,
    idempotency_key,
    rate_limit_key,
)


def _client(**kw) -> CacheClient:
    return CacheClient(FakeRedis(decode_responses=True), **kw)


def _settings(**kw) -> Settings:
    args = {"_env_file": None}
    args.update(kw)
    return Settings(**args)


# --- key builders: satu sumber, terisolasi per tenant/user ---

def test_key_builders_shape():
    assert rate_limit_key("chat", "toko-a", "budi", 123) == "rl:chat:toko-a:budi:123"
    assert idempotency_key("toko-a", "budi", "abc") == "idem:toko-a:budi:abc"
    assert deny_key("jti-1") == "jwt:deny:jti-1"


def test_key_builders_isolate_tenant_and_user():
    a = rate_limit_key("chat", "toko-a", "budi", 1)
    b = rate_limit_key("chat", "toko-b", "budi", 1)
    c = rate_limit_key("chat", "toko-a", "ani", 1)
    assert len({a, b, c}) == 3


def test_key_builders_isolate_scope():
    assert rate_limit_key("chat", "t", "u", 1) != rate_limit_key("jobs", "t", "u", 1)


# --- operasi dasar ---

@pytest.mark.asyncio
async def test_json_roundtrip():
    c = _client()
    await c.set_json("k", {"a": 1, "b": "x"}, ttl_seconds=60)
    assert await c.get_json("k") == {"a": 1, "b": "x"}


@pytest.mark.asyncio
async def test_get_json_missing_returns_none():
    assert await _client().get_json("tidak-ada") is None


@pytest.mark.asyncio
async def test_get_json_non_json_returns_none():
    c = _client()
    await c._client.set("k", "bukan json")  # noqa: SLF001 (sengaja korup)
    assert await c.get_json("k") is None


@pytest.mark.asyncio
async def test_set_if_absent_semantics():
    c = _client()
    assert await c.set_if_absent("lock", "owner-1", ttl_seconds=30) is True
    assert await c.set_if_absent("lock", "owner-2", ttl_seconds=30) is False


@pytest.mark.asyncio
async def test_exists_and_delete():
    c = _client()
    await c.set_json("k", {"a": 1}, ttl_seconds=10)
    assert await c.exists("k") is True
    await c.delete("k")
    assert await c.exists("k") is False


@pytest.mark.asyncio
async def test_ping_ok():
    assert await _client().ping() is True


@pytest.mark.asyncio
async def test_ping_never_raises_on_broken_backend():
    class Broken:
        async def ping(self):
            raise ConnectionError("mati")

    assert await CacheClient(Broken()).ping() is False


# --- fail-soft: error backend → CacheUnavailable (bukan bocor mentah) ---

class _Broken:
    async def eval(self, *a, **k):
        raise ConnectionError("mati")

    async def set(self, *a, **k):
        raise ConnectionError("mati")

    async def get(self, *a, **k):
        raise ConnectionError("mati")

    async def exists(self, *a, **k):
        raise ConnectionError("mati")

    async def delete(self, *a, **k):
        raise ConnectionError("mati")


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda c: c.incr_window("k", 60),
    lambda c: c.set_if_absent("k", "v", 60),
    lambda c: c.set_json("k", {}, 60),
    lambda c: c.get_json("k"),
    lambda c: c.exists("k"),
    lambda c: c.delete("k"),
])
async def test_backend_error_becomes_cache_unavailable(call):
    with pytest.raises(CacheUnavailable):
        await call(CacheClient(_Broken()))


@pytest.mark.asyncio
async def test_incr_window_requires_lua_support():
    """fakeredis tanpa Lua → CacheUnavailable (kontrak: bukan error mentah)."""
    with pytest.raises(CacheUnavailable):
        await _client().incr_window("rl:test", 60)


# --- factory: kosong = nonaktif, bukan error ---

def test_build_cache_none_when_url_empty():
    assert build_cache(_settings(CACHE_URL="")) is None


def test_build_cache_creates_client_when_url_set():
    c = build_cache(_settings(CACHE_URL="redis://:p@localhost:6380/0"))
    assert isinstance(c, CacheClient)
