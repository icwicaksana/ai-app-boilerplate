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

    CATATAN: `parents[2]` mengasumsikan layout checkout repo
    (`<repo>/app/skills/boot.py`). Bila diinstal sebagai package, pasang
    `SKILLS_DIR` absolut. Deployment yang ragu-ragu: cek log startup
    ("skills siap" vs "NONAKTIF") dan `/health` → `checks.skills`.
    """
    root = Path(getattr(settings, "SKILLS_DIR", "skills"))
    if root.is_absolute():
        return root
    # app/skills/boot.py -> parents[2] = akar repo
    return (Path(__file__).resolve().parents[2] / root).resolve()


def build_registry(settings, *, root: Path | None = None) -> SkillRegistry:
    """Bangun registry dari disk. Registry kosong bila fitur dimatikan."""
    if not getattr(settings, "SKILLS_ENABLED", True):
        # Keadaan normal: perilaku sistem harus identik dengan v0.8.3.
        return SkillRegistry.empty()

    root_path = root or resolve_root(settings)
    if not root_path.is_dir():
        # PENGECUALIAN SKILLS_STRICT YANG DISENGAJA: direktori tidak ada
        # bukan fatal — deployment baru boleh menyala lebih dulu, skill
        # menyusul. Tetapi skill yang ADA dan rusak TETAP menggagalkan boot
        # di bawah strict. Perbedaannya: ketiadaan vs kerusakan.
        return SkillRegistry.build([], [f"direktori skill tidak ada: {root_path}"],
                                   root=str(root_path))

    max_body = int(getattr(settings, "SKILL_BODY_MAX_CHARS", 20_000))
    skills, errors = load_skills(root_path, max_body_chars=max_body)

    max_entries = int(getattr(settings, "SKILLS_MAX_ENTRIES", 40))
    if len(skills) > max_entries:
        # Diurutkan dulu (load_skills sudah alfabetis) supaya pemotongan
        # deterministik: skill yang sama selalu selamat di setiap boot.
        errors = list(errors) + [
            f"{len(skills)} skill melebihi SKILLS_MAX_ENTRIES={max_entries} "
            f"— {max_entries} pertama dimuat, sisanya diabaikan"]
        skills = skills[:max_entries]

    if errors and getattr(settings, "SKILLS_STRICT", True):
        raise SkillStartupError(
            "skill gagal dimuat dan SKILLS_STRICT=true:\n  - "
            + "\n  - ".join(errors))
    return SkillRegistry.build(skills, errors, root=str(root_path))
