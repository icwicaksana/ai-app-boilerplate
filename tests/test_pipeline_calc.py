"""Unit: parse_amount + validate/calculate pipeline (tanpa LLM, cepat)."""

import json
from decimal import Decimal

import pytest

from app.agents.ExamplePipelineAgent import ExamplePipelineAgent, parse_amount


def test_parse_amount_valid():
    assert float(parse_amount(10)) == 10
    assert float(parse_amount("12.5")) == 12.5
    assert float(parse_amount(0)) == 0


def test_parse_amount_invalid_none():
    assert parse_amount(None) is None
    assert parse_amount("") is None
    assert parse_amount("abc") is None
    assert parse_amount("12.000,50") is None  # format lokal ditolak, minta format mesin
    assert parse_amount([1]) is None
    assert parse_amount(True) is None


def test_parse_amount_rejects_nonfinite():
    assert parse_amount(float("nan")) is None
    assert parse_amount(float("inf")) is None
    assert parse_amount(float("-inf")) is None
    assert parse_amount(Decimal("NaN")) is None
    assert parse_amount(Decimal("Infinity")) is None
    assert parse_amount("nan") is None
    assert parse_amount("Infinity") is None
    assert parse_amount("1e1000") is None  # overflow → inf → tolak
    assert parse_amount("9999999999999999999") is None  # >1e15 → tolak


def _agent_like():
    # panggil node sebagai unbound dengan self=None (node tidak pakai self)
    return ExamplePipelineAgent


def test_node_validate_filters():
    cls = _agent_like()
    state = {"raw_input": {"items": [
        {"tipe": "income", "amount": 100},
        {"tipe": "expense", "amount": "40"},
        {"tipe": "income", "amount": "abc"},   # dropped
        {"tipe": "income", "amount": 0},       # dropped (<=0)
        {"tipe": "income", "amount": -5},      # dropped
        "bukan-dict",                          # dropped
        {"tipe": "income"},                    # dropped (missing)
    ]}}
    out = cls._node_validate(None, state)
    assert out["validated"]["count"] == 2
    assert out["validated"]["dropped"] == 5
    assert all(isinstance(i["amount"], float) for i in out["validated"]["items"])


def test_node_validate_nan_inf_dropped():
    cls = _agent_like()
    state = {"raw_input": {"items": [
        {"tipe": "income", "amount": float("nan")},
        {"tipe": "income", "amount": float("inf")},
        {"tipe": "income", "amount": 50},
    ]}}
    out = cls._node_validate(None, state)  # dulu RAISE InvalidOperation
    assert out["validated"]["count"] == 1
    assert out["validated"]["dropped"] == 2


@pytest.mark.parametrize("raw", [[1, 2], 123, "halo", None, {}])
def test_node_validate_wrong_types_no_crash(raw):
    cls = _agent_like()
    out = cls._node_validate(None, {"raw_input": raw})
    assert out["validated"]["count"] == 0
    assert isinstance(out["validated"]["items"], list)


@pytest.mark.parametrize("items", [123, "x", None, {"a": 1}])
def test_node_validate_items_not_list_no_crash(items):
    cls = _agent_like()
    out = cls._node_validate(None, {"raw_input": {"items": items}})
    assert out["validated"]["count"] == 0


def test_node_calculate_decimal():
    cls = _agent_like()
    state = {"validated": {"items": [
        {"tipe": "income", "amount": 100.1},
        {"tipe": "income", "amount": 0.2},
        {"tipe": "expense", "amount": 40},
        {"tipe": "unknown", "amount": 999},  # diabaikan (tipe tak dikenal)
    ]}}
    out = cls._node_calculate(None, state)
    totals = out["totals"]
    assert totals["total_income"] == 100.3  # Decimal, bukan 100.30000000000001
    assert totals["total_expense"] == 40
    assert totals["balance"] == 60.3
    assert totals["count"] == 4
    assert totals["ignored_unknown"] == 1
    json.dumps(totals)  # harus JSON-valid (tak ada NaN/Infinity)


def test_node_calculate_nan_never():
    cls = _agent_like()
    out = cls._node_calculate(None, {"validated": {"items": [
        {"tipe": "income", "amount": float("nan")},
        {"tipe": "income", "amount": float("inf")},
    ]}})
    totals = out["totals"]
    assert totals["total_income"] == 0.0
    assert totals["balance"] == 0.0
    json.dumps(totals)


@pytest.mark.asyncio
async def test_run_agent_wrong_data_no_crash():
    from app.agents.BasePipelineAgent import BasePipelineAgent

    class Concrete(BasePipelineAgent):
        def build(self):
            return object()

    class FakeRunner:
        async def run(self, g, s, c):
            return {"status": "completed", "messages": [], "raw": {"final": "hi"}}

    ag = Concrete.__new__(Concrete)
    ag.runner = FakeRunner()
    ag.graph = object()
    for bad in ([1, 2], "halo", 123, None):
        out = await ag.run_agent(bad, {"configurable": {"thread_id": "t"}})
        assert out["status"] == "completed"
        assert out["output"] == {"reply": "hi"}

    # messages=None dari runner tidak boleh crash
    class NullRunner:
        async def run(self, g, s, c):
            return {"status": "completed", "messages": None, "raw": None}

    ag.runner = NullRunner()
    out = await ag.run_agent({"a": 1}, {"configurable": {"thread_id": "t"}})
    assert out["output"] == {"reply": ""}
    assert out["messages"] == []


def test_to_state_unserializable_no_crash():
    from app.agents.BasePipelineAgent import BasePipelineAgent

    class Concrete(BasePipelineAgent):
        def build(self):
            return object()

    ag = Concrete.__new__(Concrete)
    out = ag._to_state({"a": {1, 2}})
    assert out["raw_input"] == {"a": {1, 2}}
    assert "messages" in out
