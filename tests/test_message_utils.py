"""Unit: MessageUtils.coerce_llm_text + extract_last_ai_text (pengaman refaktor DRY)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.utils.MessageUtils import coerce_llm_text, extract_last_ai_text


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
