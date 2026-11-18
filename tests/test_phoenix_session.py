"""Regresi: session OTel Phoenix tak boleh mencemari log (A+B).

Insiden: tiap chat/stream selesai → ERROR "Failed to detach context"
(ValueError: Token ... was created in a different Context) di log BE.
Fungsi tetap benar (status=completed, 200 OK), tapi tiap stream = 1 traceback
ERROR — merusak alerting berbasis log.

Akar ganda:
 A. session_context() mengembalikan context OTel ASLI kapan pun paket
    openinference terinstall — tanpa peduli PHOENIX_ENABLED=false.
 B. Runner.stream() menahan `with session_context` melintasi `yield final`;
    server/ASGI menutup generator (aclose → GeneratorExit) dari context
    berbeda setelah result dikonsumsi → detach gagal.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import app.infra.phoenix as phoenix
from app.controllers.runner import Runner
from app.core.config import Settings


class _Snapshot:
    def __init__(self, values):
        self.values = values


class _FakeGraph:
    """Subset astream_events v2 + aget_state yang dipakai Runner."""

    def __init__(self, script, final_messages):
        self.script = script
        self.final_messages = final_messages

    async def astream_events(self, state, config=None, version="v2"):
        for item in self.script:
            yield item

    async def aget_state(self, config):
        return _Snapshot({"messages": list(self.final_messages)})


def _chat_chunk(text):
    return {"event": "on_chat_model_stream", "name": "ChatOpenAI",
            "data": {"chunk": AIMessage(content=text)}}


# --- A: session_context no-op bila tracing tak aktif ------------------------


def test_session_context_noop_saat_flag_mati():
    """Default (lifespan belum jalan / tracing mati) = nullcontext.

    Sekalipun paket openinference terinstall di venv.
    """
    assert phoenix.is_tracing_active() is False
    assert isinstance(phoenix.session_context("t"), nullcontext)


def test_setup_disabled_tidak_mengaktifkan_flag():
    """PHOENIX_ENABLED=false → setup False + flag tetap mati (tanpa network)."""
    settings = Settings(PHOENIX_ENABLED=False)
    assert phoenix.setup_phoenix_tracing(settings) is False
    assert phoenix.is_tracing_active() is False
    assert isinstance(phoenix.session_context("t"), nullcontext)


def test_flag_aktif_memberi_context_asli(monkeypatch):
    """Flag nyala = context OTel asli (guard: skip bila paket tak ada)."""
    pytest.importorskip("openinference.instrumentation")
    monkeypatch.setattr(phoenix, "_TRACING_ACTIVE", True)
    assert phoenix.is_tracing_active() is True
    assert not isinstance(phoenix.session_context("t"), nullcontext)


# --- B: teardown beda-context tak boleh ERROR --------------------------------


@pytest.mark.asyncio
async def test_stream_teardown_beda_context_tanpa_error_otel(
        monkeypatch, caplog):
    """Regresi B: generator ditutup dari task/context lain setelah result
    dikonsumsi (pola teardown ASGI) → tak ada ERROR opentelemetry.

    Pra-perbaikan: `yield final` masih di dalam `with session_context` →
    detach dari context berbeda → "Failed to detach context".
    """
    pytest.importorskip("openinference.instrumentation")
    monkeypatch.setattr(phoenix, "_TRACING_ACTIVE", True)

    final = [HumanMessage(content="hi"), AIMessage(content="halo")]
    graph = _FakeGraph([_chat_chunk("halo")], final)
    gen = Runner().stream(graph, {"messages": []},
                          {"configurable": {"thread_id": "t"}})
    events = []
    async for event in gen:
        events.append(event)
        if event["type"] == "result":
            break  # pola controller: berhenti setelah terminal event.
    assert [e["type"] for e in events] == ["started", "text", "result"]

    with caplog.at_level(logging.ERROR, logger="opentelemetry.context"):
        await asyncio.create_task(gen.aclose())  # teardown dari task lain.
    assert "Failed to detach context" not in caplog.text
