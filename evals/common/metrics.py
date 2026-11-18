"""Metrik eval deterministik (V1-E). Tanpa LLM — judge hanya sekunder."""

from __future__ import annotations


def hit_at_k(ranked_ids: list[str], expected: set[str], k: int) -> bool:
    """True bila ≥1 expected muncul di top-k."""
    return any(i in expected for i in ranked_ids[:k])


def pass_rate(results: list[dict]) -> float:
    """Fraksi kasus lolos (0.0 bila kosong — bukan 1.0!)."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.get("passed")) / len(results)


def confusion(pred_yes: list[bool], gold_yes: list[bool]) -> dict[str, int]:
    """TP/FP/TN/FN untuk keputusan biner (mis. jawab-vs-tolak)."""
    tp = fp = tn = fn = 0
    for p, g in zip(pred_yes, gold_yes):
        if p and g:
            tp += 1
        elif p and not g:
            fp += 1
        elif not p and not g:
            tn += 1
        else:
            fn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def summarize(results: list[dict]) -> dict:
    """Ringkasan suite: pass_rate + per-tag + daftar gagal."""
    by_tag: dict[str, list[dict]] = {}
    for r in results:
        for t in r.get("tags") or []:
            by_tag.setdefault(t, []).append(r)
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "pass_rate": pass_rate(results),
        "by_tag": {t: {"total": len(v), "pass_rate": pass_rate(v)}
                   for t, v in by_tag.items()},
        "failed_ids": [r["id"] for r in results if not r.get("passed")],
    }
