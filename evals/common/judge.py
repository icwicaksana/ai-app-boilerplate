"""LLM-judge sekunder (V1-E, K7): observabilitas sampled, TAK PERNAH gate.

- Sampling deterministik per id kasus (stabil antar-run).
- Rubrik faithfulness + relevance 1-5, JSON ketat.
- Fail-soft TOTAL: LLM mati / parse gagal / dimatikan = {"judged": False}.
- Pemanggil (CLI/CI) dilarang memakai skor judge untuk pass/fail.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable

RUBRIC = """Nilai JAWABAN terhadap PERTANYAAN dan BUKTI (skala 1-5 bulat).
Balas HANYA JSON: {{"faithfulness": n, "relevance": n, "notes": "..."}}.
- faithfulness: klaim jawaban didukung BUKTI (5=penuh, 1=karang).
- relevance: jawaban menjawab PERTANYAAN (5=tepat, 1=melenceng).

PERTANYAAN: {question}
BUKTI: {evidence}
JAWABAN: {answer}
"""


def sampled(case_id: str, rate: float, *, seed: int = 7) -> bool:
    """True bila kasus masuk sampel (deterministik per id)."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    h = hashlib.sha256(f"{seed}:{case_id}".encode()).hexdigest()
    return (int(h[:8], 16) % 100) < rate * 100


async def judge_case(case: dict[str, Any], result: dict[str, Any], *,
                     complete: Callable[[str], Awaitable[str]] | None,
                     rate: float = 0.0, seed: int = 7) -> dict[str, Any]:
    """Nilai 1 kasus. Tanpa complete/rate=0/tak-tersampel/gagal = unjudged."""
    base = {"judged": False, "faithfulness": None, "relevance": None,
            "notes": ""}
    if complete is None or not sampled(case.get("id", ""), rate, seed=seed):
        return base
    try:
        spec = case.get("input") or {}
        question = str(spec.get("question", spec.get("user", "")))
        chunks = (result or {}).get("chunks", result)
        evidence = json.dumps(chunks, ensure_ascii=False,
                              default=str)[:2000]
        answer = str((result or {}).get("final", result))[:2000]
        raw = await complete(RUBRIC.format(
            question=question, evidence=evidence, answer=answer))
        data = json.loads((raw or "").strip().removeprefix("```json")
                          .removesuffix("```").strip())
        f = int(data.get("faithfulness", 0))
        r = int(data.get("relevance", 0))
        if not (1 <= f <= 5 and 1 <= r <= 5):
            raise ValueError("skor di luar 1-5")
        return {"judged": True, "faithfulness": f, "relevance": r,
                "notes": str(data.get("notes", ""))[:500]}
    except Exception as exc:  # noqa: BLE001 — judge tak boleh gagalkan eval
        base["notes"] = f"judge gagal: {type(exc).__name__}"
        return base
