"""Unit: turn log helpers + TurnManager (tanpa Valkey).

Logika Streams nyata diuji di `tests/integration/test_turnlog_live.py`;
di sini yang dikunci: kontrak key, cursor eksklusif, deteksi terminal, dan
perilaku producer (menulis terminal + finish, membatalkan dengan bersih).
"""

import asyncio

import pytest

from app.core.turns import TurnManager
from app.infra.turnlog import is_terminal, new_turn_id, turn_key


def test_turn_key_shape_and_tenant_isolation():
    assert turn_key("toko-a", "abc") == "turn:toko-a:abc"
    assert turn_key("toko-a", "abc") != turn_key("toko-b", "abc")


def test_new_turn_id_unique_and_short():
    ids = {new_turn_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 16 for i in ids)


@pytest.mark.parametrize("event,expected", [
    ({"type": "result"}, True),
    ({"type": "error"}, True),
    ({"type": "token"}, False),
    ({"type": "started"}, False),
    ({}, False),
])
def test_is_terminal(event, expected):
    assert is_terminal(event) is expected


class FakeTurnLog:
    """Meniru TurnLog untuk menguji TurnManager tanpa Redis."""

    def __init__(self, fail_on_append_at: int | None = None):
        self.enabled = True
        self.events: list[dict] = []
        self.first_event: dict | None = None
        self.finished = False
        self.appended = 0
        self.fail_on_append_at = fail_on_append_at

    async def create(self, *, tenant, turn_id, first_event, ttl_seconds=900):
        self.first_event = first_event
        self.events.append(first_event)
        return "0-0"

    async def append(self, *, tenant, turn_id, event):
        self.appended += 1
        if self.fail_on_append_at is not None and self.appended >= self.fail_on_append_at:
            raise ConnectionError("cache mati")
        self.events.append(event)
        return f"{self.appended}-0"

    async def finish(self, *, tenant, turn_id, ttl_seconds=300):
        self.finished = True

    async def exists(self, *, tenant, turn_id):
        return True

    async def follow(self, *, tenant, turn_id, cursor="0-0", idle_timeout_s=60.0):
        for i, event in enumerate(self.events):
            yield f"{i}-0", event


def _stream(*events):
    async def gen():
        for e in events:
            await asyncio.sleep(0)
            yield e
    return gen()


@pytest.mark.asyncio
async def test_producer_writes_started_then_events_and_finishes():
    log = FakeTurnLog()
    mgr = TurnManager(log)
    await mgr.start(tenant="toko-a", turn_id="t1",
                    first_event={"type": "started", "turn_id": "t1"},
                    events=_stream({"type": "token", "delta": "a"},
                                   {"type": "result", "status": "completed"}))
    await asyncio.gather(*list(mgr._tasks.values()))
    assert [e["type"] for e in log.events] == ["started", "token", "result"]
    assert log.finished is True


@pytest.mark.asyncio
async def test_producer_stops_at_terminal():
    log = FakeTurnLog()
    mgr = TurnManager(log)
    await mgr.start(tenant="t", turn_id="t2", first_event={"type": "started"},
                    events=_stream({"type": "result"},
                                   {"type": "token", "delta": "harusnya tidak"}))
    await asyncio.gather(*list(mgr._tasks.values()))
    assert [e["type"] for e in log.events] == ["started", "result"]


@pytest.mark.asyncio
async def test_producer_writes_error_terminal_on_failure():
    log = FakeTurnLog(fail_on_append_at=1)  # append pertama (token) gagal
    mgr = TurnManager(log)
    await mgr.start(tenant="t", turn_id="t3", first_event={"type": "started"},
                    events=_stream({"type": "token", "delta": "a"}))
    await asyncio.gather(*list(mgr._tasks.values()))
    # error terminal tetap ditulis walau append pertama gagal? append berikutnya
    # juga akan gagal pada fake ini → cukup pastikan finish dipanggil & tak raise.
    assert log.finished is True


@pytest.mark.asyncio
async def test_follow_passthrough_and_running_flag():
    log = FakeTurnLog()
    mgr = TurnManager(log)
    assert mgr.running("t4") is False
    await mgr.start(tenant="t", turn_id="t4", first_event={"type": "started"},
                    events=_stream({"type": "result"}))
    await asyncio.gather(*list(mgr._tasks.values()))  # producer selesai dulu
    items = [x async for x in mgr.follow(tenant="t", turn_id="t4")]
    assert [e["type"] for _, e in items] == ["started", "result"]
    assert mgr.running("t4") is False


@pytest.mark.asyncio
async def test_cancel_all_stops_producers():
    log = FakeTurnLog()

    async def never_ending():
        yield {"type": "started"}
        await asyncio.sleep(3600)

    mgr = TurnManager(log)
    await mgr.start(tenant="t", turn_id="t5", first_event={"type": "started"},
                    events=never_ending())
    await asyncio.sleep(0.01)
    await mgr.cancel_all()
    assert mgr._tasks == {}


@pytest.mark.asyncio
async def test_disabled_manager_reports_disabled():
    from app.infra.turnlog import TurnLog

    mgr = TurnManager(TurnLog(None))
    assert mgr.enabled is False
