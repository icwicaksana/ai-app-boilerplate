"""Perakitan registry skill saat boot (F2). Dipisah dari lifespan agar bisa
dites tanpa menyalakan seluruh aplikasi."""

from __future__ import annotations

from pathlib import Path

from app.skills.loader import load_skills
from app.skills.registry import SkillRegistry


class SkillStartupError(Exception):
    """Skill rusak dan `SKILLS_STRICT=true` — boot harus gagal."""


def resolve_root(settings) -> Path:
    """Direktori skill absolut. Relatif dihitung dari akar repo, bukan cwd.

    Tanpa ini, menjalankan `uvicorn` dari direktori lain akan memuat nol
    skill tanpa error — dan agent diam-diam kehilangan seluruh prosedurnya.
    """
    root = Path(getattr(settings, "SKILLS_DIR", "app/skills"))
    if root.is_absolute():
        return root
    # app/skills/boot.py -> parents[2] = akar repo
    return (Path(__file__).resolve().parents[2] / root).resolve()


def build_registry(settings, *, root: Path | None = None) -> SkillRegistry:
    """Bangun registry dari disk. Registry kosong bila fitur dimatikan."""
    if not getattr(settings, "SKILLS_ENABLED", False):
        # Keadaan normal: perilaku sistem harus identik dengan v0.8.3.
        return SkillRegistry.empty()

    root_path = root or resolve_root(settings)
    if not root_path.is_dir():
        # Direktori tidak ada BUKAN error bila fitur dinyalakan tanpa skill:
        # deployment baru boleh menyala lebih dulu, skill menyusul.
        return SkillRegistry.build([], [f"direktori skill tidak ada: {root_path}"],
                                   root=str(root_path))

    skills, errors = load_skills(root_path)
    if errors and getattr(settings, "SKILLS_STRICT", True):
        raise SkillStartupError(
            "skill gagal dimuat dan SKILLS_STRICT=true:\n  - "
            + "\n  - ".join(errors))
    return SkillRegistry.build(skills, errors, root=str(root_path))
