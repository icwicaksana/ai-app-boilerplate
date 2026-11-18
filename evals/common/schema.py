"""Skema dataset eval JSONL v1 (V1-E).

Satu baris = satu kasus:
{"v": 1, "id": "...", "agent": "...", "input": {...}, "expect": {...}}

- `input`: diserahkan ke adapter agen apa adanya.
- `expect`: subset-match terhadap hasil adapter (kunci bersarang via
  path "a.b.c"; daftar = kecocokan tepat).
- `tags`: mis. ["canary"] (tier PR, deterministik) atau ["live"].
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
REQUIRED_KEYS = ("v", "id", "agent", "input", "expect")


def validate_case(case: dict[str, Any], *, source: str = "?",
                 lineno: int = 0) -> list[str]:
    """Kembalikan daftar masalah (kosong = valid)."""
    problems: list[str] = []
    where = f"{source}:{lineno}"
    if not isinstance(case, dict):
        return [f"{where}: baris bukan objek JSON"]
    for key in REQUIRED_KEYS:
        if key not in case:
            problems.append(f"{where}: kunci wajib '{key}' hilang")
    if case.get("v") != SCHEMA_VERSION:
        problems.append(f"{where}: v={case.get('v')!r} "
                        f"(didukung: {SCHEMA_VERSION})")
    if not isinstance(case.get("id"), str) or not case["id"].strip():
        problems.append(f"{where}: id wajib string tak-kosong")
    if not isinstance(case.get("input"), dict):
        problems.append(f"{where}: input wajib objek")
    if not isinstance(case.get("expect"), dict):
        problems.append(f"{where}: expect wajib objek")
    tags = case.get("tags", [])
    if not isinstance(tags, list) or any(
            not isinstance(t, str) for t in tags):
        problems.append(f"{where}: tags wajib daftar string")
    return problems


def load_suite(path: str) -> list[dict[str, Any]]:
    """Muat + validasi berkas JSONL. Raise ValueError bila ada masalah."""
    import json

    cases: list[dict[str, Any]] = []
    problems: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"{path}:{lineno}: JSON rusak ({exc})")
                continue
            problems.extend(validate_case(case, source=path, lineno=lineno))
            cases.append(case)
    seen: set[str] = set()
    for case in cases:
        cid = case.get("id")
        if cid in seen:
            problems.append(f"{path}: id duplikat {cid!r}")
        seen.add(cid)
    if problems:
        raise ValueError("dataset eval tak valid:\n" + "\n".join(problems))
    return cases


def match_expect(result: dict[str, Any],
                 expect: dict[str, Any]) -> list[str]:
    """Subset-match ekspektasi. Kembalikan daftar kegagalan (kosong = lolos).

    Kunci "a.b.c" = path bersarang (dict atau indeks list).
    Nilai daftar/dict = samakan tepat.
    """
    failures: list[str] = []
    for path, want in expect.items():
        cur: Any = result
        for part in str(path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                cur = KeyError
                break
        if cur is KeyError:
            failures.append(f"{path}: hilang (ingin {want!r})")
        elif cur != want:
            failures.append(f"{path}: dapat {cur!r} (ingin {want!r})")
    return failures
