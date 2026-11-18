"""v0.9 F0: regresi kebocoran text subagent pada graph NYATA.

Berkas ini membuktikan EMPAT hal, bukan satu:

    1. Tanpa pertahanan, text subagent BENAR-BENAR bocor ke stream.
       (Kalau ini tidak terbukti, premis F0 salah dan harus dikoreksi.)
    2. `disable_streaming=True` pada LLM child mematikan tokennya secara
       struktural — bahkan tanpa tag sama sekali.
    3. Tag `subagent` menutup jalur tool_event dan text yang lolos.
    4. Jawaban agen utama TETAP sampai ke klien.

Memakai model palsu yang benar-benar streaming, `create_agent` asli, dan
`astream_events(version="v2")` asli — tanpa panggilan LLM sungguhan.
"""

from __future__ import annotations

import json

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool

from app.core.stream_policy import StreamPolicy

RAHASIA = "RAHASIA-SUBAGENT-JANGAN-SAMPAI-KLIEN"
JAWABAN = "JAWABAN-UTAMA"


class _FakeToolModel(BaseChatModel):
    """Model palsu minimal: streaming, bisa di-bind ke tool, bisa diputar ulang.

    `GenericFakeChatModel` tidak mengimplementasikan `bind_tools` dan
    iterator-nya habis setelah sekali pakai, padahal `create_agent` memanggil
    model lebih dari sekali per request.
    """

    replies: list[AIMessage] = []
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-tool-model"

    def bind_tools(self, tools, **kwargs):  # noqa: D102
        return self

    def _pick(self) -> AIMessage:
        msg = self.replies[min(self.idx, len(self.replies) - 1)]
        self.idx += 1
        return msg

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._pick())])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """Pancarkan chunk sungguhan.

        WAJIB diimplementasikan: `BaseChatModel` TIDAK memancarkan
        `on_chat_model_stream` bila hanya `_generate` yang ada — dibuktikan
        oleh tes pertama yang sempat gagal. Tanpa ini, kebocoran tidak
        tereproduksi dan tes menjadi hijau secara palsu.
        """
        msg = self._pick()
        if msg.tool_calls:
            chunks = [
                {
                    "name": tc["name"],
                    "args": json.dumps(tc["args"]),
                    "id": tc["id"],
                    "index": i,
                    "type": "tool_call_chunk",
                }
                for i, tc in enumerate(msg.tool_calls)
            ]
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_call_chunks=chunks))
            return
        for ch in str(msg.content):
            if run_manager is not None:
                run_manager.on_llm_new_token(ch)
            yield ChatGenerationChunk(message=AIMessageChunk(content=ch))


def _model(contents: list[str], **kw) -> _FakeToolModel:
    return _FakeToolModel(replies=[AIMessage(content=c) for c in contents], **kw)


def _build_parent_child(*, tag_child: bool, child_streaming: bool = True):
    """Parent agent memanggil child agent lewat tool (subagent-as-tool)."""
    child = create_agent(
        _model([RAHASIA], disable_streaming=not child_streaming),
        tools=[],
        name="child_subagent",
    )
    child_config = {"tags": ["subagent"]} if tag_child else {}

    @tool
    async def delegate(q: str) -> str:
        """Delegasikan tugas ke pekerja lain."""
        res = await child.ainvoke({"messages": [("user", q)]},
                                  config=child_config)
        return res["messages"][-1].content

    parent = create_agent(
        _FakeToolModel(replies=[
            AIMessage(content="", tool_calls=[
                {"name": "delegate", "args": {"q": "cek"}, "id": "call-1"},
            ]),
            AIMessage(content=JAWABAN),
        ]),
        tools=[delegate],
        name="parent_main",
    )
    return parent


async def _collect(parent) -> list[dict]:
    return [ev async for ev in parent.astream_events(
        {"messages": [("user", "halo")]}, version="v2")]


def _texts(events: list[dict], policy: StreamPolicy | None) -> str:
    out = []
    for ev in events:
        if policy is not None and not policy.accept(ev):
            continue
        if ev.get("event") != "on_chat_model_stream":
            continue
        chunk = (ev.get("data") or {}).get("chunk")
        content = getattr(chunk, "content", "") if chunk is not None else ""
        if content:
            out.append(str(content))
    return "".join(out)


# --- 1. premis: kebocoran nyata -------------------------------------------

@pytest.mark.asyncio
async def test_kebocoran_terbukti_tanpa_pertahanan():
    """Tanpa StreamPolicy DAN tanpa tag, text subagent MEMANG bocor.

    Kalau tes ini gagal, kebocoran tidak tereproduksi — keputusan F0 harus
    ditinjau ulang, bukan dianggap sistemnya aman.
    """
    events = await _collect(_build_parent_child(tag_child=False))
    assert RAHASIA in _texts(events, None)


# --- 2. lapis struktural: disable_streaming -------------------------------

@pytest.mark.asyncio
async def test_disable_streaming_mematikan_token_subagent():
    """Lapis 1: tanpa tag sama sekali, text child tetap tidak muncul."""
    events = await _collect(
        _build_parent_child(tag_child=False, child_streaming=False))
    assert RAHASIA not in _texts(events, None), (
        "disable_streaming=True seharusnya membuat child tidak pernah "
        "memancarkan on_chat_model_stream")


# --- 3. lapis kebijakan: tag ----------------------------------------------

@pytest.mark.asyncio
async def test_tag_subagent_menutup_kebocoran():
    """Lapis 2: child bertag -> event-nya disaring."""
    events = await _collect(_build_parent_child(tag_child=True))
    assert RAHASIA not in _texts(events, StreamPolicy())


@pytest.mark.asyncio
async def test_delegasi_tampil_sebagai_tool_biasa():
    """K3: tidak ada jenis event SSE baru.

    Delegasi TAMPIL sebagai `tool_start`/`tool_end` biasa (nanti dinamai
    `subagent:<name>` oleh make_subagent_tool). Yang TIDAK boleh tampil
    adalah apa pun yang terjadi DI DALAM subagent — sudah ditutup oleh tes
    kebocoran di atas.
    """
    events = await _collect(_build_parent_child(tag_child=True))
    policy = StreamPolicy()
    visible = [
        ev.get("name") for ev in events
        if policy.accept(ev) and ev.get("event") == "on_tool_start"
    ]
    assert visible == ["delegate"]


# --- 4. tidak over-filter ------------------------------------------------

@pytest.mark.asyncio
async def test_jawaban_agen_utama_tetap_sampai():
    """Penyaring tidak boleh membuang jawaban yang memang untuk user."""
    events = await _collect(_build_parent_child(tag_child=True))
    assert JAWABAN in _texts(events, StreamPolicy())
