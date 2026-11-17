"""v0.9 F4: make_subagent_tool — guard build + perilaku runtime.

Model palsu + create_agent asli + tanpa jaringan. Guard (unknown/HITL/
rekursi/budget) diuji tanpa model sama sekali — build harus gagal SEBELUM
LLM disentuh.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from app.agents.subagents.registry import SUBAGENTS, SubagentSpec
from app.agents.tools.DelegationTool import (
    SUBAGENT_TOOL_PREFIX,
    SubagentConfigError,
    make_subagent_tool,
    subagent_tool_name,
)
from app.core.config import Settings


@tool
def _cek_stok(produk: str) -> str:
    """Cek stok produk (palsu, untuk tes delegasi)."""
    return f"stok {produk}: 12"


@tool("subagent__nakal")
def _delegasi_nakal(q: str) -> str:
    """Delegasi palsu untuk tes larangan rekursi."""
    return q


def _settings(**kw) -> Settings:
    base = dict(_env_file=None, JWT_SECRET="x" * 40,
                JWT_ISSUER="ai-app-boilerplate",
                LITELLM_API_KEY="test", LITELLM_MODEL="gpt-4o-mini")
    base.update(kw)
    return Settings(**base)


def _spec(**kw) -> SubagentSpec:
    base = dict(name="cek", description="cek stok",
                when_to_use="user tanya stok",
                system_prompt="Kamu pengecek stok.", tools=("dummy",))
    base.update(kw)
    return SubagentSpec(**base)


def _register(monkeypatch, spec: SubagentSpec):
    monkeypatch.setitem(SUBAGENTS, spec.name, spec)
    return spec


class _OkModel(BaseChatModel):
    replies: list = []

    @property
    def _llm_type(self) -> str:
        return "ok-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[
            ChatGeneration(message=AIMessage(content="stok A: 12"))])


class _CrashModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "crash-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("model meledak")


class _SlowModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "slow-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        import time as _t

        _t.sleep(1.5)
        return ChatResult(generations=[
            ChatGeneration(message=AIMessage(content="telat"))])


# --- guard build ---------------------------------------------------------------

def test_spec_tak_dikenal_ditolak_dengan_daftar(monkeypatch):
    _register(monkeypatch, _spec(name="ada", tools=()))
    with pytest.raises(SubagentConfigError, match="tidak ada di roster"):
        make_subagent_tool("tak-ada", tools=[], settings=_settings())


def test_tool_hilang_ditolak(monkeypatch):
    _register(monkeypatch, _spec(name="cek", tools=("tak-ada",)))
    with pytest.raises(SubagentConfigError, match="tak tersedia"):
        make_subagent_tool("cek", tools=[_cek_stok], settings=_settings())


def test_tool_approval_dilarang(monkeypatch):
    """Inti keamanan F4: subagent tak boleh pegang tool ber-approval."""
    from app.agents.tools.ExampleRefundTool import process_refund

    _register(monkeypatch, _spec(name="curang", tools=("process_refund",)))
    with pytest.raises(SubagentConfigError, match="DILARANG.*approval"):
        make_subagent_tool("curang", tools=[process_refund],
                           settings=_settings())


def test_rekursi_ditolak(monkeypatch):
    _register(monkeypatch, _spec(name="rek", tools=("subagent__nakal",)))
    with pytest.raises(SubagentConfigError, match="rekursi"):
        make_subagent_tool("rek", tools=[_delegasi_nakal],
                           settings=_settings())


def test_nama_tool_prefix_aman_openai():
    assert subagent_tool_name("cek") == "subagent__cek"
    assert SUBAGENT_TOOL_PREFIX == "subagent__"


def test_get_llm_streaming_flag():
    from app.infra.llm import get_llm

    assert get_llm(_settings(), streaming=False).disable_streaming is True
    assert get_llm(_settings()).disable_streaming is False


# --- runtime ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delegasi_ok(monkeypatch):
    _register(monkeypatch, _spec(name="cek", tools=()))
    delegate = make_subagent_tool("cek", tools=[], settings=_settings(),
                                  model=_OkModel())
    assert delegate.name == "subagent__cek"
    out = await delegate.ainvoke({"task": "cek stok A"})
    assert "[Subagent: ok]" in out
    assert "stok A: 12" in out


@pytest.mark.asyncio
async def test_delegasi_gagal_jujur(monkeypatch):
    _register(monkeypatch, _spec(name="cek", tools=()))
    delegate = make_subagent_tool("cek", tools=[], settings=_settings(),
                                  model=_CrashModel())
    out = await delegate.ainvoke({"task": "cek stok A"})
    assert "[Subagent: failed]" in out
    assert "TIDAK bisa didapat" in out


@pytest.mark.asyncio
async def test_delegasi_timeout(monkeypatch):
    _register(monkeypatch, _spec(name="lambat", tools=(), timeout_s=1))
    delegate = make_subagent_tool("lambat", tools=[], settings=_settings(),
                                  model=_SlowModel())
    out = await delegate.ainvoke({"task": "tunggu"})
    assert "[Subagent: timeout]" in out
    assert "melebihi 1 dtk" in out


@pytest.mark.asyncio
async def test_brief_tak_valid_fail_soft(monkeypatch):
    _register(monkeypatch, _spec(name="cek", tools=()))
    delegate = make_subagent_tool("cek", tools=[], settings=_settings(),
                                  model=_OkModel())
    out = await delegate.ainvoke({"task": "   "})
    assert "tidak valid" in out


@pytest.mark.asyncio
async def test_budget_diturunkan_settings_menang(monkeypatch):
    """Clamp diam-diam: anggaran = min(spec, settings)."""
    _register(monkeypatch, _spec(name="cek", tools=(), max_tokens=50_000))
    delegate = make_subagent_tool(
        "cek", tools=[], settings=_settings(SUBAGENT_MAX_TOKENS=100),
        model=_OkModel())
    out = await delegate.ainvoke({"task": "cek"})
    # 0 token terpakai < 100 → ok (clamp tak terlihat saat di bawah batas).
    assert "[Subagent: ok]" in out


# --- v1.0 R15: failures per-invokasi (bukan per-build) ---

@tool("selalu_gagal")
def _selalu_gagal(x: str) -> str:
    """Tool yang selalu gagal (untuk tes isolasi failures)."""
    raise ValueError(f"rusak: {x}")


@pytest.mark.asyncio
async def test_wrap_catatan_masuk_list_invokasi_berjalan():
    """Kegagalan tool tercatat ke list milik invokasi (contextvar)."""
    from app.agents.tools.DelegationTool import _current_failures, _wrap_tool

    async def _no_sleep(s):
        return None

    wrapped = _wrap_tool("cek", _selalu_gagal, sleep=_no_sleep)
    mine: list = []
    token = _current_failures.set(mine)
    try:
        with pytest.raises(ValueError):
            await wrapped.coroutine(x="a")
    finally:
        _current_failures.reset(token)
    assert len(mine) == 1 and mine[0].tool == "selalu_gagal"


@pytest.mark.asyncio
async def test_wrap_tanpa_invokasi_tetap_raise_tanpa_catat():
    """Di luar delegate: raise seperti biasa, tak ada list disentuh."""
    from app.agents.tools.DelegationTool import _current_failures, _wrap_tool

    async def _no_sleep(s):
        return None

    assert _current_failures.get() is None  # tak ada invokasi berjalan
    wrapped = _wrap_tool("cek", _selalu_gagal, sleep=_no_sleep)
    with pytest.raises(ValueError):
        await wrapped.coroutine(x="a")


@pytest.mark.asyncio
async def test_delegasi_dua_invokasi_gagal_tak_bocor(monkeypatch):
    """Inti R15: delegate SAMA di-invoke 2x — failures tak terakumulasi."""
    from langchain_core.messages import ToolMessage

    from app.agents.tools.DelegationTool import _current_failures

    script: list = ["call", "answer", "call", "answer"]

    class _ToolDulu(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "tool-dulu"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            from langchain_core.messages import AIMessage as _AI

            step = script.pop(0) if script else "answer"
            if step == "call":
                return ChatResult(generations=[ChatGeneration(message=_AI(
                    content="", tool_calls=[{
                        "name": "selalu_gagal", "args": {"x": "a"},
                        "id": f"call-{len(script)}", "type": "tool_call"}]))])
            n_tool = sum(isinstance(m, ToolMessage) for m in messages)
            return ChatResult(generations=[ChatGeneration(message=_AI(
                content=f"selesai setelah {n_tool} tool"))])

    _register(monkeypatch, _spec(name="cek", tools=("selalu_gagal",)))
    delegate = make_subagent_tool("cek", tools=[_selalu_gagal],
                                  settings=_settings(), model=_ToolDulu())
    out1 = await delegate.ainvoke({"task": "coba"})
    out2 = await delegate.ainvoke({"task": "coba lagi"})
    assert "[Subagent: partial]" in out1 and "[Subagent: partial]" in out2
    # Tiap laporan bawa TEPAT 1 failure milik invokasinya (dulu: invokasi
    # ke-2 membawa 2 — bocor lintas panggilan).
    assert out1.count("selalu_gagal") == 1, out1
    assert out2.count("selalu_gagal") == 1, out2
    assert _current_failures.get() is None  # dilepas rapi
