"""Tier canary PR (V1-E): deterministik, tanpa layanan live.

Semua kasus tag 'canary' dari tiap dataset harus lolos. Live tier
(tag 'live') jalan di nightly, bukan di PR.
"""

from __future__ import annotations

import pytest

from evals.adapters import chat_tools as tools_adapter
from evals.adapters import hitl_demo as hitl_adapter
from evals.adapters import pipeline as pipe_adapter
from evals.adapters import rag_answer as answer_adapter
from evals.adapters import rag_chat as chat_adapter
from evals.common.runner import run_suite
from evals.common.schema import load_suite


def _ds(name: str) -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[1] / "evals" / "datasets"
               / name)


ADAPTERS = {
    "hitl_demo": ("hitl_demo.jsonl", hitl_adapter.run),
    "pipeline": ("pipeline.jsonl", pipe_adapter.run),
    "rag_answer": ("rag_answer.jsonl", answer_adapter.run),
    "chat_tools": ("chat_tools.jsonl", tools_adapter.run),
    "rag_chat": ("rag_chat.jsonl", chat_adapter.run),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", sorted(ADAPTERS))
async def test_canary_agent(agent):
    fname, adapter = ADAPTERS[agent]
    cases = load_suite(_ds(fname))
    assert cases, fname
    rep = await run_suite(cases, adapter, tags=["canary"])
    assert rep["summary"]["pass_rate"] == 1.0, (
        agent, rep["results"])
