"""Unit harness eval (V1-E/1): skema, runner, metrik — tanpa LLM."""

from __future__ import annotations

import json

import pytest

from evals.common.metrics import confusion, hit_at_k, pass_rate, summarize
from evals.common.runner import compare_baseline, run_suite, write_artifact
from evals.common.schema import load_suite, match_expect, validate_case


def test_schema_valid_dan_tolak_rusak(tmp_path):
    ok = {"v": 1, "id": "a-1", "agent": "x", "input": {"q": "1"},
          "expect": {"r": 1}, "tags": ["canary"]}
    assert validate_case(ok) == []
    bad = {"v": 2, "id": "", "agent": "x"}
    assert len(validate_case(bad)) >= 3
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(ok) + "\n" + json.dumps(ok) + "\n",
                 encoding="utf-8")
    with pytest.raises(ValueError, match="duplikat"):
        load_suite(str(p))
    p.write_text("{bukan json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rusak"):
        load_suite(str(p))


def test_match_nested_dan_list():
    res = {"a": {"b": [{"c": 1}]}, "x": 5}
    assert match_expect(res, {"a.b.0.c": 1, "x": 5}) == []
    fails = match_expect(res, {"a.b.1.c": 1, "y": 2})
    assert len(fails) == 2


def test_metrik():
    assert hit_at_k(["a", "b"], {"b"}, 2) is True
    assert hit_at_k(["a"], {"b"}, 1) is False
    assert pass_rate([]) == 0.0
    assert pass_rate([{"passed": True}, {"passed": False}]) == 0.5
    assert confusion([True, False], [True, True]) == {
        "tp": 1, "fp": 0, "tn": 0, "fn": 1}
    s = summarize([{"id": "a", "passed": True, "tags": ["canary"]},
                   {"id": "b", "passed": False, "tags": ["live"]}])
    assert (s["total"], s["passed"], s["failed_ids"]) == (2, 1, ["b"])
    assert s["by_tag"]["canary"]["pass_rate"] == 1.0


@pytest.mark.asyncio
async def test_runner_rekam_gagal_tanpa_mati():
    async def _adapter(case, ctx):
        if case["id"] == "boom":
            raise RuntimeError("meledak")
        return {"r": case["input"]["n"] * 2}

    cases = [
        {"v": 1, "id": "ok", "agent": "x", "tags": ["canary"],
         "input": {"n": 2}, "expect": {"r": 4}},
        {"v": 1, "id": "salah", "agent": "x", "tags": ["canary"],
         "input": {"n": 2}, "expect": {"r": 5}},
        {"v": 1, "id": "boom", "agent": "x", "tags": ["live"],
         "input": {"n": 1}, "expect": {"r": 2}},
    ]
    rep = await run_suite(cases, _adapter, tags=["canary"])
    assert rep["summary"]["total"] == 2  # filter tags
    assert rep["summary"]["pass_rate"] == 0.5
    rep2 = await run_suite(cases, _adapter)
    assert rep2["summary"]["failed_ids"] == ["salah", "boom"]
    assert "meledak" in rep2["results"][2]["failures"][0]


def test_baseline_banding_dan_artefak(tmp_path):
    rep = {"summary": {"total": 1, "passed": 1, "pass_rate": 1.0},
           "results": [{"id": "a", "passed": True, "failures": []}]}
    art = write_artifact(rep, str(tmp_path / "r.json"), {"model": "m"})
    assert compare_baseline(rep, art) == []
    assert "minimum" in compare_baseline(rep, art, min_pass_rate=1.1)[0]
    assert "hilang" in compare_baseline(
        rep, str(tmp_path / "tak-ada.json"))[0]
    rep2 = {"summary": {"total": 1, "passed": 0, "pass_rate": 0.0},
            "results": [{"id": "a", "passed": False,
                         "failures": ["x"]}]}
    probs = compare_baseline(rep2, art)
    assert any("regresi" in p for p in probs)
    assert any("turun" in p for p in probs)
