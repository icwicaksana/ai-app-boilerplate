"""v0.9 F3.4 gate: body skill bertahan dari pembersihan konteks.

Skenario S1: 1× load_skill di awal + 11 giliran tool biasa sesudahnya,
total melewati trigger. Tanpa exclude_tools, body skill ikut dibersihkan
walau baru dimuat sekali. Dengan exclude, body utuh sementara tool-call
biasa di luar `keep` dibersihkan.

Simulasi memakai stack middleware ASLI (build_middleware) dan counter
karakter sebagai TokenCounter — yang diuji adalah KEPUTUSAN edit (pesan
mana yang dibersihkan), bukan penghitungan token.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.utils.MiddlewareBuilderUtils import (
    RETRIEVAL_PER_TOOL_LIMIT,
    RETRIEVAL_TOOL_NAMES,
    TOOL_CALL_PER_TOOL_LIMIT,
    build_middleware,
)
from app.core.config import Settings

SKILL_BODY = "BODY-SKILL-PROSEDUR-GARANSI:" + "p" * 9000


def _settings() -> Settings:
    return Settings(_env_file=None, JWT_SECRET="x" * 40,
                    JWT_ISSUER="ai-app-boilerplate")


def _tool_use(i: int, name: str, content: str):
    call_id = f"call-{i:02d}-{name}"
    return (
        AIMessage(content="", tool_calls=[
            {"name": name, "args": {"q": i}, "id": call_id,
             "type": "tool_call"},
        ]),
        ToolMessage(content=content, tool_call_id=call_id, name=name),
    )


def _history_12_giliran() -> list:
    msgs: list = [HumanMessage(content="halo")]
    ai, tm = _tool_use(0, "load_skill", SKILL_BODY)
    msgs.extend([ai, tm])
    for i in range(1, 12):
        ai, tm = _tool_use(i, "tool_biasa", f"biasa-{i}-" + "y" * 900)
        msgs.extend([ai, tm])
    return msgs


def _count_chars(msgs) -> int:
    return sum(len(str(getattr(m, "content", ""))) for m in msgs)


def _clear_edit(settings):
    from langchain.agents.middleware import ContextEditingMiddleware

    stack = build_middleware(settings, tools=[])
    edits = [m for m in stack if isinstance(m, ContextEditingMiddleware)]
    assert len(edits) == 1
    assert len(edits[0].edits) == 1
    return edits[0].edits[0]


def test_skill_masuk_daftar_retrieval():
    assert RETRIEVAL_TOOL_NAMES >= {
        "search_knowledge_base", "load_skill", "read_skill_file"}


def test_limit_per_tool_skill_10_biasa_5():
    from langchain.agents.middleware import ToolCallLimitMiddleware

    from app.agents.tools.SkillTools import (
        LOAD_SKILL_TOOL_NAME,
        READ_SKILL_FILE_TOOL_NAME,
    )

    class _T:
        def __init__(self, name):
            self.name = name

    stack = build_middleware(_settings(), tools=[
        _T(LOAD_SKILL_TOOL_NAME), _T(READ_SKILL_FILE_TOOL_NAME),
        _T("tool_biasa")])
    limits = {m.tool_name: m.thread_limit for m in stack
              if isinstance(m, ToolCallLimitMiddleware) and m.tool_name}
    assert limits["load_skill"] == RETRIEVAL_PER_TOOL_LIMIT == 10
    assert limits["read_skill_file"] == RETRIEVAL_PER_TOOL_LIMIT == 10
    assert limits["tool_biasa"] == TOOL_CALL_PER_TOOL_LIMIT == 5


def test_body_skill_bertahan_setelah_12_giliran():
    """Gate S1: skill dimuat sekali di awal, 11 giliran sesudahnya."""
    edit = _clear_edit(_settings())
    assert "load_skill" in list(edit.exclude_tools)
    assert "read_skill_file" in list(edit.exclude_tools)

    msgs = _history_12_giliran()
    assert _count_chars(msgs) > 8000  # trigger terlampaui, kalau tidak tes palsu
    edit.apply(msgs, count_tokens=_count_chars)

    bodies = [str(m.content) for m in msgs
              if isinstance(m, ToolMessage) and m.name == "load_skill"]
    assert len(bodies) == 1
    assert bodies[0] == SKILL_BODY  # utuh, byte-identik


def test_tool_biasa_lama_tetap_dibersihkan():
    """Kebalikan gate: exclude skill tidak mematikan pembersihan normal."""
    edit = _clear_edit(_settings())
    msgs = _history_12_giliran()
    edit.apply(msgs, count_tokens=_count_chars)

    biasa = [str(m.content) for m in msgs
             if isinstance(m, ToolMessage) and m.name == "tool_biasa"]
    assert len(biasa) == 11
    # Giliran lama dibersihkan (placeholder), 3 terakhir bertahan (keep=3).
    assert biasa[0] != f"biasa-1-{'y' * 900}"
    assert "[cleared]" in biasa[0] or "cleared" in biasa[0].lower()
    assert biasa[-1] == f"biasa-11-{'y' * 900}"
