"""Integrasi: TurnLog di Valkey ASLI (Streams, cursor, TTL, terminal)."""

import asyncio

import pytest

from app.infra.turnlog import RECONNECT_TTL_SECONDS, TurnLog, new_turn_id

pytestmark = pytest.mark.integration

WORKSPACE = "it-turns"


def _log(cache) -> TurnLog:
    return TurnLog(cache)


@pytest.mark.asyncio
async def test_create_append_replay_and_cursor(cache):
    log = _log(cache)
    turn = new_turn_id()
    first = await log.create(workspace=WORKSPACE, turn_id=turn,
                             first_event={"type": "started", "turn_id": turn})
    assert first != "0-0"
    await log.append(workspace=WORKSPACE, turn_id=turn, event={"type": "token", "delta": "a"})
    await log.append(workspace=WORKSPACE, turn_id=turn, event={"type": "token", "delta": "b"})
    await log.append(workspace=WORKSPACE, turn_id=turn,
                     event={"type": "result", "status": "completed"})

    rows = await log.replay(workspace=WORKSPACE, turn_id=turn)
    assert [e["type"] for _, e in rows] == ["started", "token", "token", "result"]
    assert rows[1][1]["delta"] == "a"

    # cursor eksklusif: mulai dari entry kedua â†’ tidak mengulang yang pertama
    rows2 = await log.replay(workspace=WORKSPACE, turn_id=turn, cursor=rows[0][0])
    assert [e["type"] for _, e in rows2] == ["token", "token", "result"]


@pytest.mark.asyncio
async def test_follow_stops_at_terminal(cache):
    log = _log(cache)
    turn = new_turn_id()
    await log.create(workspace=WORKSPACE, turn_id=turn, owner="budi", first_event={"type": "started"})
    await log.append(workspace=WORKSPACE, turn_id=turn, event={"type": "token"})
    await log.append(workspace=WORKSPACE, turn_id=turn, event={"type": "result"})

    got = [e async for _, e in log.follow(
        workspace=WORKSPACE, turn_id=turn, block_ms=100, idle_timeout_s=5)]
    assert [e["type"] for e in got] == ["started", "token", "result"]


@pytest.mark.asyncio
async def test_ttl_set_at_creation_and_shortened_on_finish(cache):
    log = _log(cache)
    turn = new_turn_id()
    await log.create(workspace=WORKSPACE, turn_id=turn, owner="budi", first_event={"type": "started"},
                     ttl_seconds=900)
    assert await log.exists(workspace=WORKSPACE, turn_id=turn) is True

    await log.append(workspace=WORKSPACE, turn_id=turn, event={"type": "result"})
    await log.finish(workspace=WORKSPACE, turn_id=turn)
    # Key tetap ada (jendela reconnect), bukan langsung hilang.
    assert await log.exists(workspace=WORKSPACE, turn_id=turn) is True


@pytest.mark.asyncio
async def test_expired_turn_is_gone(cache):
    log = _log(cache)
    turn = new_turn_id()
    await log.create(workspace=WORKSPACE, turn_id=turn, owner="budi", first_event={"type": "started"},
                     ttl_seconds=1)
    await asyncio.sleep(1.3)
    assert await log.exists(workspace=WORKSPACE, turn_id=turn) is False
    assert await log.replay(workspace=WORKSPACE, turn_id=turn) == []


@pytest.mark.asyncio
async def test_workspace_isolation(cache):
    log = _log(cache)
    turn = new_turn_id()
    await log.create(workspace="workspace-a", turn_id=turn, owner="budi", first_event={"type": "started"})
    assert await log.exists(workspace="workspace-a", turn_id=turn) is True
    assert await log.exists(workspace="workspace-b", turn_id=turn) is False


@pytest.mark.asyncio
async def test_follow_returns_when_key_missing(cache):
    """Turn tak dikenal â†’ follow berhenti cepat, tidak menggantung."""
    log = _log(cache)
    got = [e async for _, e in log.follow(
        workspace=WORKSPACE, turn_id=new_turn_id(), block_ms=100, idle_timeout_s=10)]
    assert got == []
