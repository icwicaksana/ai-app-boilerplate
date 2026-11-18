"""Unit judge sekunder + CLI eval (V1-E/2, K7: observasi, tak gate)."""

from __future__ import annotations

import json

import pytest

from evals.common.judge import judge_case, sampled


def test_sampled_deterministik():
    assert sampled("a", 0.0) is False
    assert sampled("a", 1.0) is True
    assert sampled("kasus-x", 0.5) == sampled("kasus-x", 0.5)
    hits = sum(sampled(f"c{i}", 0.5) for i in range(200))
    assert 60 < hits < 140  # kasar, bukan gate ketat


@pytest.mark.asyncio
async def test_judge_parse_dan_fail_soft():
    case = {"id": "j1", "input": {"question": "Denda?"}}
    result = {"final": "Denda 2%.", "chunks": []}

    async def _ok(prompt):
        assert "Denda?" in prompt
        return '{"faithfulness": 5, "relevance": 4, "notes": "ok"}'

    out = await judge_case(case, result, complete=_ok, rate=1.0)
    assert (out["judged"], out["faithfulness"], out["relevance"]) == (
        True, 5, 4)

    async def _rusak(prompt):
        return "bukan json"

    out2 = await judge_case(case, result, complete=_rusak, rate=1.0)
    assert out2["judged"] is False and "gagal" in out2["notes"]

    async def _boom(prompt):
        raise TimeoutError("llm mati")

    out3 = await judge_case(case, result, complete=_boom, rate=1.0)
    assert out3["judged"] is False

    out4 = await judge_case(case, result, complete=None, rate=1.0)
    assert out4["judged"] is False
    out5 = await judge_case(case, result, complete=_ok, rate=0.0)
    assert out5["judged"] is False


def test_cli_record_compare_roundtrip(tmp_path, monkeypatch):
    """CLI: record canary → compare bersih → min-rate ketat gagal."""
    from evals import run as cli

    base = tmp_path / "base.json"
    rc = cli.main(["--suite", "hitl_demo", "--tags", "canary",
                   "--record", str(base)])
    assert rc == 0
    assert json.loads(base.read_text(encoding="utf-8"))["report"][
        "summary"]["pass_rate"] == 1.0
    rc2 = cli.main(["--suite", "hitl_demo", "--tags", "canary",
                    "--compare", str(base)])
    assert rc2 == 0
    rc3 = cli.main(["--suite", "hitl_demo", "--tags", "canary",
                    "--compare", str(base), "--min-rate", "1.1"])
    assert rc3 == 1
    rc4 = cli.main(["--suite", "tak-ada"])
    assert rc4 == 2
