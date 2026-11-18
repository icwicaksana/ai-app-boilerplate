"""Runner: satu-satunya pintu ke LangGraph. Dimiliki controller, di-inject ke agent.

- Controller bikin thread_id + config.
- Agent panggil runner.run()/runner.stream() di dalam run_agent()/stream_agent().
- v0.1: stream() delta-level via astream_events v2; final diambil dari
  checkpoint (aget_state, TANPA eksekusi ulang). run() tetap ainvoke (dipakai
  pipeline sync + fallback chat).

Kontrak runner.stream (dijamin untuk SEMUA graph, termasuk fallback):
`started` selalu pertama, diakhiri TEPAT SATU `result` | `error` terminal.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, AsyncIterator

from langgraph.types import Command

from app.agents.utils.MessageUtils import split_chunk_text
from app.core.errors import UpstreamAIError
from app.core.logging import get_logger
from app.core.run_trace import (
    discard_trace,
    drain_trace,
    record_span,
    sanitize_detail,
    start_trace,
    summarize_input,
)
from app.core.stream_policy import HIDDEN_TAGS, StreamPolicy
from app.helpers.response import (
    stream_error,
    stream_error_from,
    stream_started,
    stream_thinking,
    stream_text,
    stream_tool_end,
    stream_tool_start,
)
from app.infra.phoenix import session_context

log = get_logger(__name__)


def _thread_of(config: dict) -> str:
    return str((config.get("configurable") or {}).get("thread_id", "-"))


def normalize_interrupts(raw: Any) -> list[dict[str, Any]]:
    """Normalisasi interrupt LangGraph (Interrupt | dict | list) → list[dict].

    Bentuk keluaran (kontrak wire v0.4.1, aditif):
    `{"id": str, "value": Any, "response_schema": dict|None}`.
    `id` WAJIB ada agar klien bisa menjawab per-interrupt (resume map).
    """
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[dict[str, Any]] = []
    for item in items:
        value = getattr(item, "value", None)
        if value is None and isinstance(item, dict):
            value = item.get("value", item)
        schema = getattr(item, "response_schema", None)
        if schema is None and isinstance(item, dict):
            schema = item.get("response_schema")
        out.append({
            "id": str(getattr(item, "id", None) or (
                item.get("id") if isinstance(item, dict) else "") or ""),
            "value": value,
            "response_schema": schema if isinstance(schema, dict) else None,
        })
    return out


def first_interrupt_value(interrupts: list[dict[str, Any]]) -> dict | None:
    """Nilai interrupt pertama sebagai dict (kontrak lama `interrupt`)."""
    if not interrupts:
        return None
    value = interrupts[0].get("value")
    if isinstance(value, dict):
        return value
    return {"value": value}


def _event_tags(event: Any) -> set:
    """Tag event sebagai set (aman untuk bentuk tak-terduga)."""
    tags = event.get("tags") if isinstance(event, dict) else None
    if not isinstance(tags, (list, tuple, set, frozenset)):
        return set()
    return set(tags)


def _pending_approval_count(result: dict[str, Any]) -> int:
    """Jumlah interrupt menunggu keputusan (untuk trace.pending_approvals)."""
    ints = result.get("interrupts")
    if isinstance(ints, list) and ints:
        return len(ints)
    return 1 if result.get("interrupt") else 0


class Runner:
    def __init__(self, durability: str = "async"):
        # "sync" = checkpoint ditulis sebelum step berikutnya; wajib untuk
        # alur yang bisa interrupt (HITL) agar pause tak hilang saat crash.
        self.durability = durability if durability in ("sync", "async", "exit") else "async"
        # v0.9: hanya run ROOT yang boleh memancarkan event ke klien.
        # Lihat app/core/stream_policy.py untuk alasan lengkapnya.
        self.policy = StreamPolicy()

    async def _ainvoke(self, graph, payload: Any, config: dict):
        """ainvoke dengan durability; fallback bila graph tak mendukungnya.

        Graph kustom/fake (duck-typed) bisa tidak menerima `durability` —
        jangan sampai fitur checkpoint gagal karena itu.
        """
        try:
            return await graph.ainvoke(
                payload, config=config, durability=self.durability)
        except TypeError as exc:
            if "durability" not in str(exc):
                raise
            return await graph.ainvoke(payload, config=config)

    async def run(self, graph, state: dict, config: dict) -> dict[str, Any]:
        # session Phoenix = thread_id (1 thread = 1 percakapan di UI).
        # Bila tracing tak aktif, session_context = no-op (tanpa attach OTel).
        with session_context(_thread_of(config)):
            try:
                raw = await self._ainvoke(graph, state, config)
            except UpstreamAIError:
                raise
            except (TimeoutError, asyncio.TimeoutError,
                    ConnectionError) as exc:
                raise UpstreamAIError(
                    f"AI upstream gagal ({type(exc).__name__}): {exc}") from exc

        interrupts = normalize_interrupts(
            (raw or {}).get("__interrupt__") if isinstance(raw, dict) else None)
        if interrupts:
            return {"status": "pending_approval",
                    "messages": raw.get("messages", []),
                    "interrupt": first_interrupt_value(interrupts),
                    "interrupts": interrupts}

        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        return {"status": "completed", "messages": messages,
                "interrupt": None, "interrupts": None, "raw": raw}

    async def stream(self, graph, state: dict, config: dict) -> AsyncIterator[dict[str, Any]]:
        """Delta-level (v0.1): started -> text*/thinking*/tool_* -> result | error.

        - Event granular dari `graph.astream_events(..., version="v2")`.
        - Hasil final dibaca dari checkpoint (`aget_state`) — TANPA eksekusi ulang.
        - Gagal mid-stream / gagal baca final → event error terminal
          (tak pernah raise, tak pernah gantung, tak pernah result kosong).
        - Graph tanpa `astream_events` (fake/legacy di test) → fallback single-chunk
          via run() agar kontrak `started -> result` tetap terpenuhi.
        - WS3: `result` membawa `trace` terstruktur (timing tool FIFO per nama
          + span delegasi/skill dari contextvar); jalur error TANPA trace.

        Pengecualian (terdokumentasi di schemas/stream.py): kegagalan PRE-RUN
        (validasi 400/403/404, init gagal) di level controller/agent
        menghasilkan SATU event `error` TANPA `started` — graph belum jalan.
        """
        yield stream_started()
        # WS3: kolektor hidup selama stream di task produsen ini; start/drain
        # selalu berpasangan (tiru disiplin _current_failures).
        token = start_trace()
        astream_events = getattr(graph, "astream_events", None)
        if astream_events is None:
            try:
                result = await self.run(graph, state, config)
            except (asyncio.CancelledError, GeneratorExit):
                # Cancel-safe (P1): fallback sync pun bisa dibatalkan —
                # kolektor wajib dilepas, bukan bocor.
                discard_trace(token)
                raise
            except Exception as exc:
                log.exception("runner stream fallback gagal (thread=%s)",
                              _thread_of(config))
                discard_trace(token)
                yield stream_error_from(exc)
                return
            try:
                pending_run = result.get("status") == "pending_approval"
                result["trace"] = drain_trace(
                    token, complete=not pending_run,
                    pending_approvals=_pending_approval_count(result)
                    if pending_run else None)
                yield {"type": "result", **result}
            except (asyncio.CancelledError, GeneratorExit):
                discard_trace(token)  # idempotent bila drain sudah reset
                raise
            return
        # Antre start per nama tool (FIFO: cocok untuk tool paralel bernama sama).
        pending: dict[str, deque[tuple[float, str]]] = {}
        with session_context(_thread_of(config)):
            try:
                async for event in astream_events(state, config=config, version="v2"):
                    if isinstance(event, dict) and event.get("event") == "on_tool_error":
                        # Trace-only (tanpa SSE): policy tetap menolaknya di bawah.
                        self._track_tool_error(event, pending)
                        continue
                    # v0.9: saring SEBELUM dipetakan. Event dari run
                    # bersarang (subagent, klasifikasi internal) tidak boleh
                    # pernah menjadi text SSE.
                    if not self.policy.accept(event):
                        continue
                    self._track_tool_event(event, pending)
                    for out in self._map_stream_event(event):
                        yield out
                # Di DALAM try (P1): cancel saat baca checkpoint final wajib
                # melepas kolektor juga — di luar try = trace bocor.
                final = await self._final_result_event(graph, config)
            except (asyncio.CancelledError, GeneratorExit):
                # Klien putus / task dibatalkan: JANGAN yield setelah putus.
                discard_trace(token)
                raise
            except Exception as exc:
                log.exception("runner stream gagal (thread=%s)", _thread_of(config))
                discard_trace(token)
                yield stream_error_from(exc)
                return
        # Keluar dari session OTel SEBELUM yield terminal: server/ASGI menutup
        # generator (aclose → GeneratorExit) dari context berbeda setelah
        # result dikonsumsi — detach di dalam blok ini selalu di context yang
        # sama dengan attach (anti "Token was created in a different Context").
        if final is None:  # checkpoint tak terbaca = gagal, bukan result kosong
            discard_trace(token)
            yield stream_error(detail="gagal membaca state akhir graph")
        else:
            try:
                pending_run = final.get("status") == "pending_approval"
                self._flush_pending_tools(pending, pending_run=pending_run)
                final["trace"] = drain_trace(
                    token, complete=not pending_run,
                    pending_approvals=_pending_approval_count(final)
                    if pending_run else None)
                yield final
            except (asyncio.CancelledError, GeneratorExit):
                discard_trace(token)  # idempotent bila drain sudah reset
                raise

    @staticmethod
    def _track_tool_event(event: dict,
                          pending: dict[str, deque[tuple[float, str]]]) -> None:
        """Timing tool FIFO per nama → span `kind=tool`. Tak pernah raise.

        Dipanggil HANYA untuk event lolos policy; cek tag ganda agar event
        bertag subagent/internal tak pernah jadi span tool (WS3).
        """
        try:
            kind = event.get("event", "")
            if kind not in ("on_tool_start", "on_tool_end"):
                return
            if _event_tags(event) & HIDDEN_TAGS:
                return
            name = str(event.get("name", "tool") or "tool")
            if kind == "on_tool_start":
                data = event.get("data") or {}
                # P2: input non-dict diteruskan apa adanya (summarize_input
                # menangani str/list/None + meredaksi terpusat).
                summary = summarize_input(data.get("input"))
                pending.setdefault(name, deque()).append(
                    (time.perf_counter(), summary))
                return
            queue = pending.get(name)
            if queue:
                started, summary = queue.popleft()
                ms = int((time.perf_counter() - started) * 1000)
                record_span(tool=name, kind="tool", status="ok", ms=ms,
                            attempts=1, input_summary=summary)
            else:
                record_span(tool=name, kind="tool", status="ok", ms=0,
                            attempts=1, detail="orphan_end")
        except Exception:
            log.exception("runner catat timing tool gagal")

    @staticmethod
    def _track_tool_error(event: dict,
                          pending: dict[str, deque[tuple[float, str]]]) -> None:
        """`on_tool_error` → span failed (trace-only, tanpa SSE). Tak pernah raise.

        P1: catat KELAS error saja — pesan mentah dibuang (bisa membawa
        secret/PII argumen tool). `Exception` → `type(err).__name__`;
        string/dict mentah → label generik `ToolError`.
        """
        try:
            if _event_tags(event) & HIDDEN_TAGS:
                return
            name = str(event.get("name", "tool") or "tool")
            data = event.get("data") or {}
            err = data.get("error", data.get("err", ""))
            if isinstance(err, BaseException):
                detail = sanitize_detail(type(err).__name__)
            elif err:
                detail = sanitize_detail("ToolError")
            else:
                detail = ""
            queue = pending.get(name)
            if queue:
                started, summary = queue.popleft()
                ms = int((time.perf_counter() - started) * 1000)
                record_span(tool=name, kind="tool", status="failed", ms=ms,
                            attempts=1, input_summary=summary, detail=detail)
            else:
                record_span(tool=name, kind="tool", status="failed", ms=0,
                            attempts=1, detail=detail or "orphan_error")
        except Exception:
            log.exception("runner catat tool error gagal")

    @staticmethod
    def _flush_pending_tools(pending: dict[str, deque[tuple[float, str]]],
                             *, pending_run: bool) -> None:
        """Start tanpa end saat drain: `pending` (HITL) atau `skipped`."""
        status = "pending" if pending_run else "skipped"
        for name, queue in pending.items():
            while queue:
                _, summary = queue.popleft()
                record_span(tool=name, kind="tool", status=status, ms=0,  # type: ignore[arg-type]
                            attempts=1, input_summary=summary,
                            detail="tanpa tool_end")

    @staticmethod
    def _map_stream_event(event: dict) -> list[dict[str, Any]]:
        """Petakan 1 astream_event v2 → 0..n event SSE. Tak pernah raise."""
        try:
            kind = event.get("event", "")
            if kind == "on_chat_model_stream":
                chunk = (event.get("data") or {}).get("chunk")
                if chunk is None:
                    return []
                text, thinking = split_chunk_text(chunk)
                out: list[dict[str, Any]] = []
                if text:
                    out.append(stream_text(text))
                if thinking:
                    out.append(stream_thinking(thinking))
                return out
            if kind == "on_tool_start":
                data = event.get("data") or {}
                tool_input = data.get("input")
                return [stream_tool_start(
                    str(event.get("name", "tool")),
                    tool_input if isinstance(tool_input, dict) else {},
                )]
            if kind == "on_tool_end":
                return [stream_tool_end(str(event.get("name", "tool")))]
            return []
        except Exception:
            log.exception("runner petakan stream event gagal")
            return []

    async def _final_result_event(self, graph, config: dict) -> dict[str, Any] | None:
        """Bangun event result dari checkpoint terakhir (tanpa eksekusi ulang).

        Interrupt dibaca dari `snapshot.interrupts` — BUKAN dari
        `snapshot.values`. Terbukti empiris: `aget_state().values` tidak pernah
        memuat `__interrupt__`; interrupt hidup di `StateSnapshot.interrupts`
        (v0.4.1). Sebelumnya jalur streaming selalu melaporkan `completed`
        padahal graf berhenti menunggu approval.

        Gagal baca → None (pemanggil wajib yield `stream_error`, BUKAN result
        kosong).
        """
        messages: list = []
        interrupts: list[dict[str, Any]] = []
        try:
            snapshot = await graph.aget_state(config)
            values = getattr(snapshot, "values", None) or {}
            if isinstance(values, dict):
                found = values.get("messages", [])
                messages = list(found) if isinstance(found, list) else []
            interrupts = normalize_interrupts(getattr(snapshot, "interrupts", ()))
        except Exception:
            log.exception("runner baca final state gagal (thread=%s)", _thread_of(config))
            return None
        status = "pending_approval" if interrupts else "completed"
        return {"type": "result", "status": status, "messages": messages,
                "interrupt": first_interrupt_value(interrupts),
                "interrupts": interrupts or None, "raw": {}}

    async def resume(self, graph, resume_value: dict | Any, config: dict) -> dict[str, Any]:
        """Lanjutkan graph yang terjeda: `Command(resume=...)` (v0.6).

        `resume_value` boleh nilai tunggal (satu interrupt) atau map
        `{interrupt_id: value}` bila ada beberapa interrupt paralel.
        Kontrak: raise UpstreamAIError (bukan mentah) agar seragam.
        """
        try:
            with session_context(_thread_of(config)):
                raw = await self._ainvoke(graph, Command(resume=resume_value), config)
        except UpstreamAIError:
            raise
        except Exception as exc:
            raise UpstreamAIError(f"Resume HITL gagal: {exc}") from exc
        interrupts = normalize_interrupts(
            (raw or {}).get("__interrupt__") if isinstance(raw, dict) else None)
        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        return {"status": "pending_approval" if interrupts else "completed",
                "messages": messages,
                "interrupt": first_interrupt_value(interrupts),
                "interrupts": interrupts or None, "raw": raw}
