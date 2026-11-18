"""CLI eval (V1-E): `python -m evals.run --suite rag_answer --tags canary`.

Opsi: --all, --tags a b, --record BASE.json, --compare BASE.json,
--min-rate 1.0, --judge-rate 0.0 (K7: observasi saja), --out LAPORAN.json,
--seed 7. Exit: 0 gate lolos, 1 gate gagal, 2 salah pakai.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

SUITES = ("hitl_demo", "pipeline", "rag_answer", "chat_tools", "rag_chat")


def _load(agent: str):
    from evals.common.schema import load_suite

    return load_suite(str(ROOT / "datasets" / f"{agent}.jsonl"))


def _adapter(agent: str):
    import importlib

    return importlib.import_module(f"evals.adapters.{agent}").run


def _judge_complete_factory():
    async def _complete(prompt: str) -> str:
        import os

        import litellm

        resp = await litellm.acompletion(
            model=os.environ.get("EVAL_JUDGE_MODEL",
                                 "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            base_url=os.environ.get("LITELLM_BASE_URL") or None,
            api_key=os.environ.get("LITELLM_API_KEY") or None,
            max_tokens=300, temperature=0.0)
        return str(resp.choices[0].message.content or "")

    return _complete


async def _amain(args) -> int:
    from evals.common.judge import judge_case
    from evals.common.runner import (compare_baseline, run_suite,
                                     write_artifact)

    suites = list(SUITES) if args.all else [args.suite]
    if not args.all and args.suite not in SUITES:
        print(f"suite tak dikenal: {args.suite} (pilih: {', '.join(SUITES)})",
              file=sys.stderr)
        return 2
    complete = _judge_complete_factory() if args.judge_rate > 0 else None
    all_results: list[dict] = []
    for agent in suites:
        cases = _load(agent)
        rep = await run_suite(cases, _adapter(agent),
                              tags=args.tags, seed=args.seed)
        if complete is not None:
            for r, c in zip(rep["results"], [x for x in cases
                             if args.tags is None or set(args.tags)
                             & set(x.get("tags") or [])]):
                r["judge"] = await judge_case(
                    c, r.get("result") or {}, complete=complete,
                    rate=args.judge_rate, seed=args.seed)
        all_results.extend(rep["results"])
        print(f"{agent}: {rep['summary']['passed']}/"
              f"{rep['summary']['total']} lolos")
    from evals.common.metrics import summarize

    report = {"seed": args.seed, "summary": summarize(all_results),
              "results": all_results}
    if args.out:
        write_artifact(report, args.out,
                       {"suites": suites, "tags": args.tags})
        print(f"artefak: {args.out}")
    if args.record:
        write_artifact(report, args.record,
                       {"suites": suites, "tags": args.tags})
        print(f"baseline direkam: {args.record}")
    problems: list[str] = []
    if args.compare:
        problems += compare_baseline(report, args.compare,
                                     min_pass_rate=args.min_rate)
    elif args.min_rate < 1.0 or not args.record:
        # Tanpa --compare/--record: gate = min-rate atas run ini.
        rate = float(report["summary"]["pass_rate"])
        if rate < args.min_rate:
            problems.append(
                f"pass_rate {rate:.3f} < minimum {args.min_rate}")
    for p in problems:
        print(f"GAGAL: {p}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Runner eval v1.0.")
    ap.add_argument("--suite", default="hitl_demo",
                    help="nama suite (abaikan bila --all)")
    ap.add_argument("--all", action="store_true",
                    help="jalankan semua suite")
    ap.add_argument("--tags", nargs="*", default=None,
                    help="filter tags (mis. canary)")
    ap.add_argument("--record", default="",
                    help="rekam baseline ke berkas JSON")
    ap.add_argument("--compare", default="",
                    help="bandingkan dengan baseline JSON")
    ap.add_argument("--min-rate", type=float, default=1.0)
    ap.add_argument("--judge-rate", type=float, default=0.0,
                    help="fraksi sampel judge LLM (K7: observasi saja)")
    ap.add_argument("--out", default="", help="tulis laporan JSON")
    ap.add_argument("--seed", type=int, default=7)
    return asyncio.run(_amain(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
