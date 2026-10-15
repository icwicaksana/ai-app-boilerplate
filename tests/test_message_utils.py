"""Unit: MessageUtils.coerce_llm_text + extract_last_ai_text (pengaman refaktor DRY)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.utils.MessageUtils import (
    coerce_llm_text,
    extract_last_ai_text,
    extract_reasoning_text,
    split_chunk_text,
)


def test_coerce_str_strip():
    assert coerce_llm_text("  halo  ") == "halo"
    assert coerce_llm_text(None) == ""
    assert coerce_llm_text("") == ""


def test_coerce_blocks_only_text():
    blocks = [
        {"type": "text", "text": "halo "},
        {"type": "tool_use", "text": "JANGAN IKUT"},
        {"type": "image_url", "text": "JANGAN IKUT"},
        {"type": "output_text", "text": "dunia"},
    ]
    assert coerce_llm_text(blocks) == "halo dunia"


def test_extract_last_ai_ignores_tool():
    msgs = [
        HumanMessage(content="stok?"),
        AIMessage(content="", tool_calls=[{"name": "check_stock", "args": {}, "id": "1"}]),
        ToolMessage(content="42 unit", tool_call_id="1"),
        AIMessage(content="Stok 42 unit tersedia."),
    ]
    assert extract_last_ai_text(msgs) == "Stok 42 unit tersedia."


def test_extract_empty():
    assert extract_last_ai_text([]) == ""
    assert extract_last_ai_text([HumanMessage(content="hi")]) == ""
    assert extract_last_ai_text([ToolMessage(content="x", tool_call_id="1")]) == ""


def test_reasoning_never_in_answer_coerce():
    """Aturan keras: reasoning/thinking/redacted tak pernah masuk teks jawaban."""
    blocks = [
        {"type": "text", "text": "jawab"},
        {"type": "reasoning", "reasoning": "RAHASIA-1"},
        {"type": "thinking", "thinking": "RAHASIA-2"},
        {"type": "redacted_thinking", "data": "RAHASIA-3"},
        {"type": "reasoning_content", "text": "RAHASIA-4"},
    ]
    assert coerce_llm_text(blocks) == "jawab"
    assert extract_last_ai_text([AIMessage(content=blocks)]) == "jawab"


def test_extract_reasoning_text_sources():
    msg = AIMessage(
        content=[{"type": "reasoning", "reasoning": "pikir-A"}],
        additional_kwargs={"reasoning_content": "pikir-B",
                           "reasoning_items": [{"summary": "pikir-C"}]},
    )
    out = extract_reasoning_text(msg)
    assert "pikir-A" in out and "pikir-B" in out and "pikir-C" in out
    assert extract_reasoning_text(AIMessage(content="biasa")) == ""


def test_split_chunk_preserves_spaces_and_splits():
    text, think = split_chunk_text(AIMessage(content=" halo "))
    assert (text, think) == (" halo ", "")  # delta TIDAK di-strip
    text, think = split_chunk_text(AIMessage(content=[
        {"type": "text", "text": "a"},
        {"type": "thinking", "thinking": "b"},
        {"type": "tool_use", "text": "c"},
        {"type": "redacted_thinking", "data": "d"},
    ]))
    assert (text, think) == ("a", "b")
