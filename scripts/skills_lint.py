"""Lint skill + roster subagent: `python -m scripts.skills_lint [--dir skills]`.

Dipakai operator sebelum deploy dan oleh CI fork. Exit 0 = bersih
(boleh ada warning); exit 1 = ada error.

Cek skill: loader penuh (parse + E01-E08 + E08 folder) + info ukuran.
Cek roster: nama valid, field terisi, tool approval-gated (ERROR), tool
delegasi bersarang (ERROR), tool tak dikenal (WARNING — fork boleh punya
tool sendiri yang tak terlihat di sini).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.agents.subagents.registry import (  # noqa: E402
    SPEC_NAME_RE,
    SUBAGENTS,
)
from app.agents.tools.DelegationTool import SUBAGENT_TOOL_PREFIX  # noqa: E402
from app.core.hitl import HITL_POLICY  # noqa: E402
from app.skills.loader import load_skills  # noqa: E402
from app.skills.registry import SkillRegistry  # noqa: E402

#: Nama tool factory-bound (tak ada di TOOLS global, tetapi sah).
_KNOWN_FACTORY_TOOLS = frozenset({
    "search_knowledge_base", "save_memory", "save_profile_patch",
    "load_skill", "read_skill_file",
})


def lint_skills(root: Path) -> dict:
    """Jalankan loader penuh. Kembalikan laporan (boleh error)."""
    skills, errors = load_skills(root)
    reg = SkillRegistry.build(skills, errors, root=str(root))
    index = reg.index()
    return {
        "root": str(root),
        "count": len(skills),
        "errors": list(errors),
        "manifest": reg.manifest_id,
        "index_chars": len(index),
        "skills": [
            {"name": s.name, "body_chars": len(s.body),
             "tags": list(s.tags), "files": list(s.files)}
            for s in reg.skills
        ],
    }


def lint_subagents() -> dict:
    """Validasi roster ter-import. Kembalikan laporan (boleh error)."""
    try:
        from app.agents.tools.ToolRegistry import TOOLS
    except Exception:
        TOOLS = []
    known = {getattr(t, "name", "?") for t in TOOLS} | _KNOWN_FACTORY_TOOLS
    errors: list[str] = []
    warnings: list[str] = []
    specs = []
    for name in sorted(SUBAGENTS):
        spec = SUBAGENTS[name]
        specs.append(name)
        if not SPEC_NAME_RE.match(name):
            errors.append(f"subagent {name!r}: nama tak valid.")
        if not spec.description.strip() or not spec.when_to_use.strip():
            errors.append(f"subagent {name!r}: description/when_to_use kosong.")
        for tool_name in spec.tools:
            pol = HITL_POLICY.get(tool_name)
            if pol is not None and pol.enabled:
                errors.append(
                    f"subagent {name!r}: tool approval-gated {tool_name!r} "
                    f"DILARANG (bangun ulang akan gagal).")
            if tool_name.startswith(SUBAGENT_TOOL_PREFIX):
                errors.append(
                    f"subagent {name!r}: tool delegasi {tool_name!r} DILARANG "
                    f"(rekursi ditolak).")
            if tool_name not in known:
                warnings.append(
                    f"subagent {name!r}: tool {tool_name!r} tak terlihat di "
                    f"ToolRegistry — pastikan tersedia di build induk.")
    return {"count": len(specs), "specs": specs,
            "errors": errors, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint skill + subagent.")
    parser.add_argument("--dir", default="skills",
                        help="direktori skill relatif/absolut")
    args = parser.parse_args(argv)

    # Import agar spec terdaftar (roster terisi saat import).
    import app.agents.subagents  # noqa: F401

    root = Path(args.dir)
    root = root if root.is_absolute() else (REPO_ROOT / root)
    skills = lint_skills(root)
    subs = lint_subagents()

    print(f"skills: {skills['count']} di {skills['root']} "
          f"(manifest {skills['manifest'][:12]}, indeks {skills['index_chars']} char)")
    for s in skills["skills"]:
        extra = f" +{len(s['files'])} L3" if s["files"] else ""
        print(f"  OK {s['name']} ({s['body_chars']} char{extra})")
    for e in skills["errors"]:
        print(f"  ERROR {e}")
    print(f"subagents: {subs['count']} ({', '.join(subs['specs']) or '-'})")
    for w in subs["warnings"]:
        print(f"  WARNING {w}")
    for e in subs["errors"]:
        print(f"  ERROR {e}")

    n_err = len(skills["errors"]) + len(subs["errors"])
    if n_err:
        print(f"GAGAL: {n_err} error.", file=sys.stderr)
        return 1
    print("BERSIH.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
