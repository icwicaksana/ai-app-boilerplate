"""Unit: ticket WS (sekali pakai) + Origin allowlist (CSWSH)."""

import pytest

from app.core.ws_ticket import TicketStore, origin_allowed, ticket_key
from tests._fake_cache import FakeCache


@pytest.mark.parametrize("origin,allowed,expected", [
    (None, ["http://localhost:3000"], True),          # non-browser
    ("http://localhost:3000", ["http://localhost:3000"], True),
    ("http://localhost:3000/", ["http://localhost:3000"], True),  # slash dinormalkan
    ("HTTP://LOCALHOST:3000", ["http://localhost:3000"], True),   # case-insensitive
    ("https://evil.example", ["http://localhost:3000"], False),
    ("http://localhost:3001", ["http://localhost:3000"], False),
])
def test_origin_allowlist(origin, allowed, expected):
    assert origin_allowed(origin, allowed) is expected


def test_ticket_key_prefix():
    assert ticket_key("abc") == "wsticket:abc"


@pytest.mark.asyncio
async def test_ticket_issue_and_consume_once():
    cache = FakeCache()
    store = TicketStore(cache, ttl_seconds=30)
    ticket, ttl = await store.issue(workspace="toko-a", user="budi")
    assert ttl == 30 and ticket
    ident = await store.consume(ticket)
    assert ident and ident.workspace == "toko-a" and ident.user == "budi"
    # sekali pakai
    assert await store.consume(ticket) is None


@pytest.mark.asyncio
async def test_ticket_unknown_or_empty():
    store = TicketStore(FakeCache(), ttl_seconds=30)
    assert await store.consume("tidak-ada") is None
    assert await store.consume("") is None


@pytest.mark.asyncio
async def test_ticket_without_cache_raises_on_issue():
    from app.infra.cache import CacheUnavailable

    store = TicketStore(None, ttl_seconds=30)
    with pytest.raises(CacheUnavailable):
        await store.issue(workspace="t", user="u")
    assert await store.consume("apa-saja") is None


@pytest.mark.asyncio
async def test_ticket_cache_down_is_rejected_softly():
    cache = FakeCache()
    store = TicketStore(cache, ttl_seconds=30)
    ticket, _ = await store.issue(workspace="t", user="u")
    cache.down = True
    assert await store.consume(ticket) is None  # fail-closed untuk auth
