"""v0.2: middleware stack, CORS, structured pipeline + demo chat (offline)."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agents.AgentContext import AgentContext
from app.agents.ExamplePipelineAgent import ExamplePipelineAgent, NarrativeInsight
from app.agents.examples.OrderExtractorDemo import Order, OrderExtractorDemo
from app.agents.utils.MiddlewareBuilderUtils import build_middleware
from app.controllers.registry import CHAT_AGENTS, JOB_AGENTS
from app.core.config import Settings
from app.main import create_app


def _settings():
    return Settings(_env_file=None, LITELLM_BASE_URL="http://localhost:4000",
                    LITELLM_API_KEY="x", LITELLM_MODEL="test-model")


# --- middleware stack ---

def test_middleware_order_and_types():
    from langchain.agents.middleware import (
        ContextEditingMiddleware,
        ToolCallLimitMiddleware,
        ToolErrorMiddleware,
    )
    from app.agents.tools.ToolRegistry import TOOLS

    stack = build_middleware(_settings(), list(TOOLS))
    kinds = [type(m).__name__ for m in stack]
    # [limit-global, limit-per-tool..., context-editing, tool-error], tanpa HITL
    assert kinds[0] == "ToolCallLimitMiddleware"
    assert kinds[-2] == "ContextEditingMiddleware"
    assert kinds[-1] == "ToolErrorMiddleware"
    assert len(stack) == 3 + len(TOOLS)
    assert all(isinstance(m, (ToolCallLimitMiddleware, ContextEditingMiddleware,
                              ToolErrorMiddleware)) for m in stack)


def test_middleware_never_raises_without_tools():
    assert isinstance(build_middleware(_settings(), None), list)
    assert isinstance(build_middleware(_settings(), []), list)


def test_tool_error_message_id():
    from app.agents.utils.MiddlewareBuilderUtils import _tool_error_message
    from unittest.mock import MagicMock

    req = MagicMock()
    req.tool_name = "check_stock"
    msg = _tool_error_message(ValueError("boom"), req)
    assert "check_stock" in msg and "ValueError" in msg


# --- CORS ---

def test_cors_headers_present():
    client = TestClient(create_app())
    res = client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000",
                 "Access-Control-Request-Method": "POST"},
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


# --- pipeline structured (fake LLM, tanpa network) ---

class _Extractor:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeLLM:
    def __init__(self, script, fallback_text="fallback bebas"):
        self.script = script
        self.fallback_text = fallback_text

    def with_structured_output(self, schema):
        assert schema is NarrativeInsight
        return _Extractor(self.script)

    def invoke(self, prompt):
        return AIMessage(content=self.fallback_text)


def _agent_with(fake):
    ag = ExamplePipelineAgent.__new__(ExamplePipelineAgent)
    ag.llm = fake
    return ag


def test_narrate_structured_numbers_from_python():
    ag = _agent_with(_FakeLLM([
        NarrativeInsight(anomaly="Boros di jajan.", suggestion="Masak sendiri."),
    ]))
    out = ag._node_narrate({"totals": {"total_income": 100.0, "total_expense": 40.0,
                                       "balance": 60.0, "count": 2}})
    assert "100.0" in out["final"] and "60.0" in out["final"]  # angka dari Python
    assert "Masak sendiri" in out["final"]
    assert out["structured"]["balance"] == 60.0
    assert out["structured"]["suggestion"] == "Masak sendiri."
    assert len(out["messages"]) == 2


def test_narrate_self_correct_once_then_ok():
    ag = _agent_with(_FakeLLM([
        ValueError("invalid JSON"),
        NarrativeInsight(anomaly="A.", suggestion="B."),
    ]))
    out = ag._node_narrate({"totals": {"total_income": 1.0}})
    assert "structured" in out
    assert out["structured"]["anomaly"] == "A."


def test_narrate_fallback_deterministic_when_structured_dead():
    """v0.3.1: structured gagal 2x → render deterministik dari Python.

    Jaminan "LLM tak bisa karang angka" dipertahankan bahkan saat fallback:
    angka 100% dari totals, insight kosong, tanpa `structured`.
    """
    ag = _agent_with(_FakeLLM([ValueError("x"), ValueError("y")],
                              fallback_text="ringkasan bebas"))
    out = ag._node_narrate({"totals": {"total_income": 1.0}})
    assert "1.0" in out["final"]  # angka dari Python, bukan LLM
    assert "ringkasan bebas" not in out["final"]  # LLM tak dipakai samasekali
    assert "structured" not in out  # → output.data absen (kontrak aditif)


def test_render_empty_insight():
    text = ExamplePipelineAgent._render_summary({"total_income": 0}, NarrativeInsight())
    assert "Data belum tersedia." in text


# --- demo chat terpisah (tidak terdaftar!) ---

def test_demo_not_registered_but_buildable_offline():
    assert "order_extractor_demo" not in CHAT_AGENTS
    assert "order_extractor_demo" not in JOB_AGENTS
    from langgraph.checkpoint.memory import InMemorySaver

    from app.controllers.runner import Runner

    ctx = AgentContext(settings=_settings(), checkpointer=InMemorySaver(),
                       runner=Runner())
    ag = OrderExtractorDemo(ctx)  # construct saja (tanpa network)
    assert ag.graph is not None


def test_order_schema_validation():
    ok = Order(customer_name="Budi", customer_phone="081", items=[
        {"item_id": "A", "quantity": 2}], payment_method="cod")
    assert ok.items[0].quantity == 2
    with pytest.raises(Exception):
        Order(customer_name="B", customer_phone="1", items=[
            {"item_id": "A", "quantity": 0}], payment_method="cod")
