"""Gate regresi eval di CI (acceptance roadmap v1.0 #1).

Canary harus cocok baseline tercatat. Ubah dataset/ekspektasi yang
disengaja → regenerasi: `python -m evals.run --all --tags canary
--record evals/baselines/canary.json` dan commit baseline baru.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_canary_cocok_baseline():
    from evals import run as cli

    base = str(ROOT / "evals" / "baselines" / "canary.json")
    assert Path(base).exists(), "baseline hilang — rekam dulu (lihat docstring)"
    rc = cli.main(["--all", "--tags", "canary", "--compare", base])
    assert rc == 0, "regresi eval canary vs baseline"
