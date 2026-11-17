"""Scaffold skill baru: `python -m scripts.new_skill <nama> [--dir skills]`.

Menyalin `skills/_template/SKILL.md`, mengisi nama, dan MEMVALIDASI hasil
sebelum menulis — scaffold yang menghasilkan berkas rusak lebih buruk
daripada tidak ada scaffold.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.skills.parser import parse_skill  # noqa: E402
from app.skills.validate import NAME_RE  # noqa: E402

_FALLBACK_TEMPLATE = """\
---
name: {name}
description: [ISI SATU KALIMAT: prosedur apa ini]
when_to_use: [ISI: kapan agen harus memuat skill ini]
tags: [contoh]
---

# [ISI JUDUL PROSEDUR]

[ISI LANGKAH BERNOMOR]
"""


def render_skill_file(name: str) -> str:
    """Render isi SKILL.md untuk `name`. Raise ValueError bila tak valid."""
    if not NAME_RE.match(name):
        raise ValueError(
            f"nama skill tak valid: {name!r} "
            f"(huruf kecil/angka/dash, 2-63, diawali huruf/angka).")
    template_path = REPO_ROOT / "skills" / "_template" / "SKILL.md"
    if template_path.is_file():
        text = template_path.read_text(encoding="utf-8-sig")
        rendered = text.replace("name: nama-skill", f"name: {name}", 1)
    else:
        rendered = _FALLBACK_TEMPLATE.format(name=name)
    # Jaminan: hasil scaffold selalu lolos parser+validator.
    parse_skill(rendered, source=f"{name}/SKILL.md")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buat skill baru dari template.")
    parser.add_argument("name", help="nama skill (mis. sop-garansi)")
    parser.add_argument("--dir", default="skills",
                        help="direktori skill relatif/absolut")
    args = parser.parse_args(argv)

    try:
        rendered = render_skill_file(args.name)
    except ValueError as exc:
        print(f"GAGAL: {exc}", file=sys.stderr)
        return 2

    root = Path(args.dir)
    root = root if root.is_absolute() else (REPO_ROOT / root)
    target = root / args.name / "SKILL.md"
    if target.exists():
        print(f"GAGAL: {target} sudah ada — pilih nama lain.", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"OK: {target}")
    print("Langkah berikut:")
    print(f"  1. Isi SKILL.md (description + when_to_use + body prosedur).")
    print(f"  2. python -m scripts.skills_lint   # validasi")
    print(f"  3. Nyalakan SKILLS_ENABLED=true dan uji via chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
