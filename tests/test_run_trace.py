"""WS3: trace terstruktur di StreamResult (kontrak docs/TRACE-CONTRACT.md).

FakeGraph meniru subset `astream_events` v2 + `aget_state` yang dipakai Runner
(pola tests/test_sse_streaming.py) + interrupt snapshot (pola
tests/test_sse_envelope.py). Tanpa LLM, tanpa Valkey, tanpa tmp_path.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.subagents.registry import SUBAGENTS, SubagentSpec
from app.agents.tools.DelegationTool import _delegate_run, make_subagent_tool
from app.controllers.runner import Runner
from app.core.run_trace import (
    MAX_INPUT_SUMMARY_CHARS,
    MAX_SPANS,
    REDACTED,
    TRACE_VERSION,
    discard_trace,
    drain_trace,
    record_span,
    sanitize_detail,
    start_trace,
    summarize_input,
    trace_active,
)
from app.schemas.subagent import ToolFailure

GOOD = Path(__file__).parent / "fixtures" / "skills_boot" / "good"


# --- FakeGraph ---------------------------------------------------------------

class _Snapshot:
    def __init__(self, values, interrupts=()):
        self.values = values
        self.interrupts = tuple(interrupts)


class FakeGraph:
    """Graph scriptable: `script` = list event v2 / Exception untuk raise."""

    def __init__(self, script, final_messages, interrupts=()):
        self.script = script
        self.final_messages = final_messages
        self.interrupts = interrupts

    async def astream_events(self, state, config=None, version="v2"):
        for item in self.script:
            if isinstance(item, Exception):
                raise item
            yield item

    async def aget_state(self, config):
        return _Snapshot({"messages": list(self.final_messages)},
                         interrupts=self.interrupts)

    async def ainvoke(self, state, config=None):
        return {"messages": list(self.final_messages)}


class _InvokeOnlyGraph:
    """Legacy: tanpa astream_events → fallback via run()."""

    def __init__(self, final_messages):
        self.final_messages = final_messages

    async def ainvoke(self, state, config=None):
        return {"messages": list(self.final_messages)}


def _tok(text):
    return {"event": "on_chat_model_stream", "name": "m",
            "data": {"chunk": AIMessage(content=text)}}


def _tstart(name, input=None, tags=()):
    evt = {"event": "on_tool_start", "name": name,
           "data": {"input": dict(input or {})}}
    if tags:
        evt["tags"] = list(tags)
    return evt


def _tend(name, tags=()):
    evt = {"event": "on_tool_end", "name": name, "data": {}}
    if tags:
        evt["tags"] = list(tags)
    return evt


def _terr(name, err):
    return {"event": "on_tool_error", "name": name,
            "data": {"error": err}}


async def _collect(graph, state=None, config=None):
    return [e async for e in Runner().stream(
        graph, state or {"messages": []},
        config or {"configurable": {"thread_id": "t"}})]


def _result(events):
    assert events[-1]["type"] == "result", events
    return events[-1]


# --- kolektor ----------------------------------------------------------------

def test_tanpa_trace_record_noop_tak_raise():
    assert trace_active() is False
    record_span(tool="x", kind="tool", status="ok")  # no-op, tak raise
    assert trace_active() is False


def test_cap_20_truncated():
    token = start_trace()
    try:
        assert trace_active() is True
        for i in range(MAX_SPANS + 5):
            record_span(tool=f"t{i}", kind="tool", status="ok", ms=i)
        trace = drain_trace(token, complete=True)
    except Exception:
        discard_trace(token)
        raise
    assert trace["trace_version"] == TRACE_VERSION == 1
    assert trace["complete"] is True
    assert len(trace["spans"]) == MAX_SPANS
    assert trace["truncated"] is True
    assert trace["spans"][0]["tool"] == "t0"  # 20 PERTAMA dipertahankan
    assert "pending_approvals" not in trace
    assert trace_active() is False  # dilepas rapi


def test_input_summary_cap_500_dan_sanitasi():
    long = "x" * 2000 + "<<<palsu>>> kata\ndengan newline"
    out = summarize_input({"data": long})
    assert len(out) <= MAX_INPUT_SUMMARY_CHARS == 500
    assert "<<<" not in out and "\n" not in out
    assert sanitize_detail("a<<<b") == "a‹‹‹b"


def test_stream_success_trace_aditif():
    from app.helpers.response import stream_success
    from app.schemas.stream import StreamResult

    lama = stream_success(output={"reply": "x"})  # pemanggil lama tak berubah
    assert lama["type"] == "result" and lama["trace"] is None
    assert StreamResult(output={"reply": "x"}).trace is None
    trace = {"trace_version": 1, "complete": True, "spans": [],
             "truncated": False}
    baru = stream_success(output={"reply": "x"}, trace=trace)
    assert baru["trace"] == trace


def test_jobs_sync_tanpa_trace():
    """runner.run langsung (jobs sync) TAK PERNAH bawa trace."""

    async def go():
        return await Runner().run(
            _InvokeOnlyGraph([AIMessage(content="ok")]), {"messages": []},
            {"configurable": {"thread_id": "t"}})

    result = asyncio.run(go())
    assert "trace" not in result
    assert trace_active() is False


# --- runner: FIFO / orphan / tag-filter --------------------------------------

@pytest.mark.asyncio
async def test_fifo_timing_per_nama():
    script = [
        _tstart("cek", {"n": 1}),
        _tstart("cek", {"n": 2}),
        _tend("cek"),
        _tend("cek"),
    ]
    events = await _collect(FakeGraph(script, [AIMessage(content="ok")]))
    kinds = [e["type"] for e in events]
    assert kinds == ["started", "tool_start", "tool_start", "tool_end",
                     "tool_end", "result"]
    spans = _result(events)["trace"]["spans"]
    assert [(s["tool"], s["kind"], s["status"]) for s in spans] == [
        ("cek", "tool", "ok"), ("cek", "tool", "ok")]
    assert '"n": 1' in spans[0]["input_summary"]  # FIFO: start-1 ↔ end-1
    assert '"n": 2' in spans[1]["input_summary"]
    assert all(s["ms"] >= 0 and s["attempts"] == 1 for s in spans)


@pytest.mark.asyncio
async def test_orphan_start_completed_jadi_skipped():
    script = [_tstart("jalan", {"x": 1}), _tok("ok")]
    events = await _collect(FakeGraph(script, [AIMessage(content="ok")]))
    trace = _result(events)["trace"]
    assert trace["complete"] is True and trace["truncated"] is False
    (span,) = trace["spans"]
    assert span["status"] == "skipped" and span["detail"] == "tanpa tool_end"
    assert '"x": 1' in span["input_summary"]


@pytest.mark.asyncio
async def test_orphan_end_ms_nol():
    events = await _collect(
        FakeGraph([_tend("hantu")], [AIMessage(content="ok")]))
    (span,) = _result(events)["trace"]["spans"]
    assert (span["tool"], span["status"], span["ms"]) == ("hantu", "ok", 0)
    assert span["detail"] == "orphan_end"


@pytest.mark.asyncio
async def test_tag_subagent_tak_masuk_tools():
    script = [
        _tstart("anak", {"a": 1}, tags=["subagent", "subagent:cek"]),
        _tend("anak", tags=["subagent"]),
        _tstart("induk", {"b": 2}),
        _tend("induk"),
    ]
    events = await _collect(FakeGraph(script, [AIMessage(content="ok")]))
    # SSE: event bertag subagent tak pernah jadi tool_start/tool_end.
    assert [e["type"] for e in events] == ["started", "tool_start",
                                           "tool_end", "result"]
    assert events[1]["tool"] == "induk"
    # Trace: hanya span induk; tak ada span "anak".
    spans = _result(events)["trace"]["spans"]
    assert [s["tool"] for s in spans] == ["induk"]


@pytest.mark.asyncio
async def test_tool_error_span_failed_tanpa_sse():
    script = [_tstart("rusak", {"x": 1}), _terr("rusak", "boom")]
    events = await _collect(FakeGraph(script, [AIMessage(content="ok")]))
    assert [e["type"] for e in events] == ["started", "tool_start", "result"]
    (span,) = _result(events)["trace"]["spans"]
    # P1: pesan mentah "boom" dibuang — yang tercatat kelas error saja.
    assert span["status"] == "failed" and span["detail"] == "ToolError"


@pytest.mark.asyncio
async def test_mid_error_tanpa_trace():
    script = [_tstart("jalan", {"x": 1}), TimeoutError("llm putus")]
    events = await _collect(FakeGraph(script, []))
    assert [e["type"] for e in events] == ["started", "tool_start", "error"]
    assert "trace" not in events[-1]  # StreamError tak berubah
    assert trace_active() is False  # kolektor dilepas walau abort


@pytest.mark.asyncio
async def test_legacy_tanpa_astream_punya_trace():
    events = await _collect(
        _InvokeOnlyGraph([HumanMessage(content="hi"),
                          AIMessage(content="legacy")]))
    assert [e["type"] for e in events] == ["started", "result"]
    trace = _result(events)["trace"]
    assert trace == {"trace_version": 1, "complete": True, "spans": [],
                     "truncated": False}


# --- HITL pending + resume replay --------------------------------------------

@pytest.mark.asyncio
async def test_hitl_pending_trace_belum_complete():
    inter = [{"id": "i1", "value": {"action": "refund"},
              "response_schema": None}]
    script = [_tstart("process_refund", {"amount": 600000}), _tok("tunggu")]
    events = await _collect(
        FakeGraph(script, [AIMessage(content="tunggu")], interrupts=inter))
    result = _result(events)
    assert result["status"] == "pending_approval"
    trace = result["trace"]
    assert trace["complete"] is False
    assert trace["pending_approvals"] == 1
    (span,) = trace["spans"]
    assert span["tool"] == "process_refund" and span["status"] == "pending"


@pytest.mark.asyncio
async def test_resume_replay_trace_identik():
    """Turn log replay = byte-identik incl trace (tanpa ubah TurnManager)."""
    from app.core.turns import TurnManager
    from app.infra.turnlog import TurnLog
    from tests._fake_cache import FakeCache

    script = [_tstart("cek", {"n": 1}), _tend("cek"), _tok("ok")]
    events = await _collect(
        FakeGraph(script, [AIMessage(content="ok")]))
    asli = _result(events)
    assert asli["trace"]["spans"]

    mgr = TurnManager(TurnLog(FakeCache()))

    async def gen():
        for e in events:
            if e.get("type") == "started":
                continue  # TurnManager tulis started sbg first_event
            yield e

    await mgr.start(workspace="toko-a", turn_id="t1", owner="budi",
                    first_event={"type": "started", "turn_id": "t1"},
                    events=gen())
    await asyncio.gather(*list(mgr._tasks.values()))
    replayed = await mgr.turnlog.replay(workspace="toko-a", turn_id="t1")
    hasil = [e for _, e in replayed if e.get("type") == "result"]
    assert len(hasil) == 1
    assert hasil[0]["trace"] == asli["trace"]  # JSON round-trip utuh
    # Serialisasi turn log aman (tak ada objek non-JSON di trace).
    json.dumps(asli["trace"], ensure_ascii=False)


# --- agen teruskan trace ------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_teruskan_trace_lewat_rebuild():
    """stream_success bangun ulang objek — trace wajib diteruskan agen."""

    class Chat(BaseChatAgent):
        AGENT_NAME = "t"
        SYSTEM_PROMPT = "x"
        TOOLS = []

    script = [_tstart("cek", {"n": 1}), _tend("cek"), _tok("ok")]
    ag = Chat.__new__(Chat)
    ag.runner = Runner()
    ag.graph = FakeGraph(script, [AIMessage(content="ok")])
    events = [e async for e in ag.stream_agent(
        "hi", {"configurable": {"thread_id": "t"}})]
    assert [e["type"] for e in events] == ["started", "tool_start",
                                           "tool_end", "text", "result"]
    result = events[-1]
    assert result["output"] == {"reply": "ok"}
    assert result["trace"]["complete"] is True
    assert [s["tool"] for s in result["trace"]["spans"]] == ["cek"]


# --- delegasi ----------------------------------------------------------------

def _spec(**kw) -> SubagentSpec:
    base = dict(name="cek", description="cek stok",
                when_to_use="user tanya stok",
                system_prompt="Kamu pengecek stok.", tools=())
    base.update(kw)
    return SubagentSpec(**base)


class _ChildOk:
    def __init__(self):
        self.tags = None

    async def ainvoke(self, payload, config=None):
        self.tags = (config or {}).get("tags")
        return {"messages": [AIMessage(content="stok A: 12")]}


class _ChildBoom:
    async def ainvoke(self, payload, config=None):
        raise RuntimeError("model meledak")


class _ChildSlow:
    async def ainvoke(self, payload, config=None):
        await asyncio.sleep(2)
        return {"messages": [AIMessage(content="telat")]}


@pytest.mark.asyncio
async def test_delegasi_ok_record_span():
    token = start_trace()
    child = _ChildOk()
    out = await _delegate_run(_spec(), child, "cek stok A", 5.0, 10_000,
                              time.perf_counter(), [])
    trace = drain_trace(token, complete=True)
    assert "[Subagent: ok]" in out and "stok A: 12" in out  # to_text utuh
    assert child.tags and "subagent" in child.tags
    (span,) = trace["spans"]
    assert span["tool"] == "subagent__cek" and span["kind"] == "subagent"
    assert span["status"] == "ok" and span["detail"] == ""
    assert "cek stok A" in span["input_summary"]


@pytest.mark.asyncio
async def test_delegasi_partial_bawa_detail_gagal():
    fails = [ToolFailure(tool="cek_stok", error_class="permanent",
                         attempts=1, detail="ValueError")]
    token = start_trace()
    out = await _delegate_run(_spec(), _ChildOk(), "cek", 5.0, 10_000,
                              time.perf_counter(), fails)
    (span,) = drain_trace(token, complete=True)["spans"]
    assert "[Subagent: partial]" in out
    assert span["status"] == "partial"
    assert "cek_stok (permanent)" in span["detail"]


@pytest.mark.asyncio
async def test_delegasi_gagal_jujur_record_span():
    token = start_trace()
    out = await _delegate_run(_spec(), _ChildBoom(), "cek" * 2000, 5.0,
                              10_000, time.perf_counter(), [])
    (span,) = drain_trace(token, complete=True)["spans"]
    assert "[Subagent: failed]" in out and "TIDAK bisa didapat" in out
    assert span["status"] == "failed"
    assert len(span["input_summary"]) <= 500  # prompt panjang dipotong


@pytest.mark.asyncio
async def test_delegasi_timeout_record_span():
    token = start_trace()
    out = await _delegate_run(_spec(), _ChildSlow(), "tunggu", 0.01,
                              10_000, time.perf_counter(), [])
    (span,) = drain_trace(token, complete=True)["spans"]
    assert "[Subagent: timeout]" in out
    assert span["status"] == "timeout" and "0.01 dtk" in span["detail"]


@pytest.mark.asyncio
async def test_delegasi_brief_invalid_skipped(monkeypatch):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    from app.core.config import Settings

    class _OkModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "ok-model"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[
                ChatGeneration(message=AIMessage(content="x"))])

    monkeypatch.setitem(SUBAGENTS, "cek", _spec())
    settings = Settings(_env_file=None, JWT_SECRET="x" * 40,
                        JWT_ISSUER="ai-app-boilerplate",
                        LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini")
    delegate = make_subagent_tool("cek", tools=[], settings=settings,
                                  model=_OkModel())
    token = start_trace()
    out = await delegate.ainvoke({"task": "   "})
    (span,) = drain_trace(token, complete=True)["spans"]
    assert "tidak valid" in out
    assert span["kind"] == "subagent" and span["status"] == "skipped"


# --- skill -------------------------------------------------------------------

def _registry(**kw):
    from app.skills.loader import load_skills
    from app.skills.registry import SkillRegistry

    skills, errors = load_skills(GOOD)
    assert not errors
    return SkillRegistry.build(skills, root=str(GOOD), **kw)


@pytest.mark.asyncio
async def test_skill_load_ok_dan_unknown():
    from app.agents.tools.SkillTools import make_load_skill_tool

    tool = make_load_skill_tool(_registry())
    token = start_trace()
    ok = await tool.ainvoke({"name": "alpha"})
    bad = await tool.ainvoke({"name": "tak-ada"})
    spans = drain_trace(token, complete=True)["spans"]
    assert "Body skill alpha" in ok and "tidak dikenal" in bad
    assert [(s["tool"], s["kind"], s["status"]) for s in spans] == [
        ("load_skill", "skill", "ok"),
        ("load_skill", "skill", "skipped")]
    assert spans[1]["detail"] == "unknown"


@pytest.mark.asyncio
async def test_skill_read_ok_traversal_unknown():
    from app.agents.tools.SkillTools import make_read_skill_file_tool

    tool = make_read_skill_file_tool(_registry())
    token = start_trace()
    ok = await tool.ainvoke({"name": "alpha",
                             "path": "references/detail.md"})
    jahat = await tool.ainvoke({"name": "alpha", "path": "../SKILL.md"})
    tak_ada = await tool.ainvoke({"name": "tak-ada", "path": "x.md"})
    hilang = await tool.ainvoke({"name": "alpha", "path": "tak-ada.md"})
    spans = drain_trace(token, complete=True)["spans"]
    assert "Detail Alpha" in ok and "ditolak" in jahat
    assert "tidak dikenal" in tak_ada and "tidak ada" in hilang
    assert [(s["status"], s["detail"]) for s in spans] == [
        ("ok", ""), ("skipped", "traversal_ditolak"),
        ("skipped", "unknown"), ("skipped", "file_hilang")]
    assert all(s["tool"] == "read_skill_file" and s["kind"] == "skill"
               for s in spans)


# --- redaksi terpusat P0 -------------------------------------------------------

def test_summarize_redaksi_denylist_key():
    out = summarize_input({"username": "budi", "password": "rahasia123",
                           "nik": "3201010101010001"})
    assert "rahasia123" not in out and "3201010101010001" not in out
    assert REDACTED in out and "budi" in out  # non-secret utuh


def test_summarize_redaksi_varian_key_tanpa_regex():
    out = summarize_input({"access_token": "abc", "api-key": "k",
                           "kata sandi": "x"})
    assert "abc" not in out and '"k"' not in out
    assert out.count(REDACTED) == 3


def test_summarize_redaksi_teks_bebas_delegasi():
    # Brief.task berupa teks bebas: tampak secret → seluruh ringkasan redacted.
    assert summarize_input("password saya rahasia123") == REDACTED
    assert summarize_input("cek stok A") == "cek stok A"  # non-secret utuh


def test_summarize_angka_kecil_bukan_secret():
    out = summarize_input({"n": 1, "amount": 600000})
    assert '"n": 1' in out and "600000" in out


def test_record_span_coerce_enum_invalid():
    token = start_trace()
    try:
        record_span(tool="x", kind="aneh", status="meledak")  # type: ignore[arg-type]
        spans = drain_trace(token, complete=True)["spans"]
    except Exception:
        discard_trace(token)
        raise
    assert spans[0]["kind"] == "tool" and spans[0]["status"] == "failed"


# --- golden skema beku --------------------------------------------------------

_GOLDEN_KINDS = {"tool", "subagent", "skill"}
_GOLDEN_STATUSES = {"ok", "partial", "failed", "timeout", "skipped",
                    "pending"}


def _assert_trace_valid(trace) -> None:
    """Tiap `result.trace` WAJIB valid vs skema beku §1 + §8 kontrak."""
    assert trace["trace_version"] == TRACE_VERSION == 1
    assert isinstance(trace["complete"], bool)
    assert isinstance(trace["spans"], list)
    assert len(trace["spans"]) <= MAX_SPANS
    assert isinstance(trace["truncated"], bool)
    if trace["complete"]:
        assert "pending_approvals" not in trace
    else:
        assert isinstance(trace["pending_approvals"], int)
        assert trace["pending_approvals"] >= 0
    for s in trace["spans"]:
        assert isinstance(s["tool"], str) and s["tool"]
        assert s["kind"] in _GOLDEN_KINDS
        assert s["status"] in _GOLDEN_STATUSES
        assert isinstance(s["ms"], int) and s["ms"] >= 0
        assert isinstance(s["attempts"], int) and s["attempts"] >= 1
        for key in ("input_summary", "detail"):
            val = s[key]
            assert isinstance(val, str) and len(val) <= 500
            assert "\n" not in val and "\r" not in val
    json.dumps(trace, ensure_ascii=False)  # JSON-aman untuk turn log


@pytest.mark.asyncio
async def test_golden_semua_trace_valid_vs_skema_beku():
    """Baterai jalur × validasi skema: ok/error/pending/legacy/skill."""
    traces = []
    traces.append(_result(await _collect(
        FakeGraph([_tstart("cek", {"n": 1}), _tend("cek")],
                  [AIMessage(content="ok")]) ))["trace"])
    traces.append(_result(await _collect(
        FakeGraph([_tstart("rusak", {}), _terr("rusak", "x")],
                  [AIMessage(content="ok")]) ))["trace"])
    inter = [{"id": "i1", "value": {"a": 1}, "response_schema": None}]
    traces.append(_result(await _collect(
        FakeGraph([_tstart("t", {})], [AIMessage(content="w")],
                  interrupts=inter)))["trace"])
    traces.append(_result(await _collect(
        _InvokeOnlyGraph([AIMessage(content="legacy")]) ))["trace"])
    assert traces[2]["complete"] is False  # pending ikut baterai
    for trace in traces:
        _assert_trace_valid(trace)


# --- abort / isolasi / orphan-error / pending-replay ---------------------------

class _HangingGraph(FakeGraph):
    """Yield 1 event lalu gantung di Event (untuk uji aclose mid-stream)."""

    def __init__(self, gate: asyncio.Event):
        super().__init__([], [AIMessage(content="tak-tercapai")])
        self.gate = gate

    async def astream_events(self, state, config=None, version="v2"):
        yield _tstart("jalan", {"x": 1})
        await self.gate.wait()
        yield _tend("jalan")


@pytest.mark.asyncio
async def test_abort_aclose_discard_kolektor():
    gate = asyncio.Event()
    stream = Runner().stream(
        _HangingGraph(gate), {"messages": []},
        {"configurable": {"thread_id": "t"}})
    try:
        assert (await stream.__anext__())["type"] == "started"
        assert (await stream.__anext__())["type"] == "tool_start"
        assert trace_active() is True  # kolektor hidup mid-stream
    finally:
        await stream.aclose()  # GeneratorExit di dalam async-for
        gate.set()
    assert trace_active() is False  # dilepas, tak bocor


@pytest.mark.asyncio
async def test_dua_task_interleave_isolasi():
    async def _jalan(tool_name, n):
        script = [_tstart(tool_name, {"n": n}), _tend(tool_name)]
        events = await _collect(
            FakeGraph(script, [AIMessage(content="ok")]))
        return _result(events)["trace"]

    t1, t2 = await asyncio.gather(_jalan("alat_A", 1), _jalan("alat_B", 2))
    assert [s["tool"] for s in t1["spans"]] == ["alat_A"]  # tak bocor silang
    assert [s["tool"] for s in t2["spans"]] == ["alat_B"]
    assert '"n": 1' in t1["spans"][0]["input_summary"]
    assert '"n": 2' in t2["spans"][0]["input_summary"]
    _assert_trace_valid(t1)
    _assert_trace_valid(t2)


@pytest.mark.asyncio
async def test_orphan_error_tanpa_start():
    events = await _collect(
        FakeGraph([_terr("hantu", "boom")], [AIMessage(content="ok")]))
    (span,) = _result(events)["trace"]["spans"]
    assert span["status"] == "failed" and span["ms"] == 0
    assert span["detail"] == "ToolError"  # pesan "boom" dibuang (P1)

    events = await _collect(
        FakeGraph([_terr("hantu", ValueError("x"))],
                  [AIMessage(content="ok")]))
    (span,) = _result(events)["trace"]["spans"]
    assert span["detail"] == "ValueError"  # Exception → nama kelas

    events = await _collect(
        FakeGraph([_terr("hantu", "")], [AIMessage(content="ok")]))
    (span,) = _result(events)["trace"]["spans"]
    assert span["detail"] == "orphan_error"  # info kosong → orphan_error


@pytest.mark.asyncio
async def test_pending_replay_round_trip():
    """Pending + pending_approvals utuh lewat turn log (replay byte-identik)."""
    from app.core.turns import TurnManager
    from app.infra.turnlog import TurnLog
    from tests._fake_cache import FakeCache

    inter = [{"id": "i1", "value": {"action": "refund"},
              "response_schema": None},
             {"id": "i2", "value": {"action": "void"},
              "response_schema": None}]
    script = [_tstart("process_refund", {"amount": 600000}), _tok("tunggu")]
    events = await _collect(
        FakeGraph(script, [AIMessage(content="tunggu")], interrupts=inter))
    asli = _result(events)
    assert asli["status"] == "pending_approval"
    assert asli["trace"]["complete"] is False
    assert asli["trace"]["pending_approvals"] == 2
    _assert_trace_valid(asli["trace"])

    mgr = TurnManager(TurnLog(FakeCache()))

    async def gen():
        for e in events:
            if e.get("type") == "started":
                continue
            yield e

    await mgr.start(workspace="toko-a", turn_id="t9", owner="budi",
                    first_event={"type": "started", "turn_id": "t9"},
                    events=gen())
    await asyncio.gather(*list(mgr._tasks.values()))
    replayed = await mgr.turnlog.replay(workspace="toko-a", turn_id="t9")
    hasil = [e for _, e in replayed if e.get("type") == "result"]
    assert len(hasil) == 1
    assert hasil[0]["trace"] == asli["trace"]
    assert hasil[0]["trace"]["pending_approvals"] == 2
