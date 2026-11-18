"""Runner suite eval (V1-E): deterministik + artefak baseline.

- Seed acak global (PYTHONHASHSEED di luar; `random.seed` di sini).
- Tiap kasus: adapter(case, ctx) → result dict → match_expect.
- Artefak JSON: hasil + ringkasan + lingkungan (model/pin) untuk
  perbandingan baseline (`--compare` gagal bila pass_rate turun).
"""

from __future__ import annotations

import json
import random
import time
from typing import Any, Awaitable, Callable

from evals.common.metrics import summarize
from evals.common.schema import match_expect

Adapter = Callable[[dict[str, Any], Any],
                   Awaitable[dict[str, Any]]]


async def run_suite(cases: list[dict[str, Any]], adapter: Adapter,
                    ctx: Any = None, *, seed: int = 7,
                    tags: list[str] | None = None) -> dict[str, Any]:
    """Jalankan kasus (filter tags opsional). Tak pernah raise per-kasus:
    adapter melempar = kasus gagal + pesan tercatat."""
    random.seed(seed)
    selected = [c for c in cases
                if tags is None or set(tags) & set(c.get("tags") or [])]
    results: list[dict[str, Any]] = []
    for case in selected:
        t0 = time.perf_counter()
        try:
            result = await adapter(case, ctx)
            failures = match_expect(result if isinstance(result, dict)
                                    else {}, case["expect"])
            results.append({
                "id": case["id"], "agent": case["agent"],
                "tags": case.get("tags") or [], "passed": not failures,
                "failures": failures, "result": result,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            })
        except Exception as exc:  # noqa: BLE001 — eval tak boleh mati 1 kasus
            results.append({
                "id": case["id"], "agent": case["agent"],
                "tags": case.get("tags") or [], "passed": False,
                "failures": [f"adapter raise: {type(exc).__name__}: {exc}"],
                "result": None,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            })
    return {"seed": seed, "summary": summarize(results), "results": results}


def write_artifact(report: dict[str, Any], path: str,
                   env: dict[str, Any] | None = None) -> str:
    """Tulis artefak JSON (hasil + ringkasan + env). Return path."""
    payload = {"env": env or {}, "report": report}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2,
                  default=str)
    return path


def compare_baseline(report: dict[str, Any], baseline_path: str,
                     *, min_pass_rate: float = 1.0) -> list[str]:
    """Bandingkan dengan baseline tersimpan. Kembalikan masalah.

    Aturan: pass_rate tak boleh turun dari baseline; kasus yang dulu
    lolos tak boleh gagal sekarang. Baseline hilang = masalah (eksplisit,
    bukan lolos-semu) — kecuali min_pass_rate dipakai sebagai gate tunggal.
    """
    import os

    summary = report.get("summary") or {}
    problems: list[str] = []
    rate = float(summary.get("pass_rate", 0.0))
    if rate < min_pass_rate:
        problems.append(f"pass_rate {rate:.3f} < minimum {min_pass_rate}")
    if not os.path.exists(baseline_path):
        problems.append(f"baseline hilang: {baseline_path} "
                        "(rekam dulu dengan --record)")
        return problems
    with open(baseline_path, encoding="utf-8") as fh:
        base = json.load(fh)
    base_results = {(r.get("id")): r
                    for r in (base.get("report") or {}).get("results", [])}
    for r in report.get("results", []):
        old = base_results.get(r.get("id"))
        if old is not None and old.get("passed") and not r.get("passed"):
            problems.append(
                f"regresi: {r.get('id')} dulu lolos kini gagal "
                f"({r.get('failures')})")
    old_rate = float((base.get("report") or {}).get("summary", {})
                     .get("pass_rate", 0.0))
    if rate < old_rate:
        problems.append(
            f"pass_rate turun {old_rate:.3f} → {rate:.3f}")
    return problems
