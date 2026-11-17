"""Pemuatan skill dari disk (F2). Satu-satunya modul skill yang menyentuh I/O.

KEPUTUSAN: kegagalan dikumpulkan, bukan langsung dilempar.
    Satu skill rusak tidak boleh membuat proses lain tidak bisa melaporkan
    skill mana yang rusak. `load_skills` mengembalikan (skills, errors), dan
    pemanggil (lifespan) yang memutuskan fatal atau tidak lewat
    `SKILLS_STRICT`. Health selalu menampilkan daftar error supaya tidak ada
    kegagalan yang hanya hidup di log.

Direktori berawalan `_` dilewati: `_template/` adalah cetakan untuk operator,
bukan skill aktif. Tanpa aturan ini, template akan muncul di indeks dan
memakan slot.
"""

from __future__ import annotations

from pathlib import Path

from app.skills.model import Skill, SkillError
from app.skills.parser import parse_skill
from app.skills.validate import validate_folder_name

SKILL_FILENAME = "SKILL.md"
IGNORED_PREFIX = "_"


def discover(root: Path) -> list[Path]:
    """Direktori skill yang sah, terurut alfabetis (deterministik)."""
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir()
         if p.is_dir() and not p.name.startswith(IGNORED_PREFIX)),
        key=lambda p: p.name,
    )


def load_skills(root: Path) -> tuple[list[Skill], list[str]]:
    """Muat semua skill di `root`. Tidak pernah raise untuk skill rusak."""
    skills: list[Skill] = []
    errors: list[str] = []

    for folder in discover(root):
        path = folder / SKILL_FILENAME
        if not path.is_file():
            errors.append(
                f"{folder.name}: tidak ada {SKILL_FILENAME} "
                f"(setiap direktori skill wajib punya berkas ini)")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{folder.name}: gagal membaca {path.name}: {exc}")
            continue
        try:
            skill = parse_skill(text, source=f"{folder.name}/{SKILL_FILENAME}")
            validate_folder_name(skill.name, folder.name, source=folder.name)
        except SkillError as exc:
            errors.append(str(exc))
            continue
        skills.append(skill)

    return skills, errors


def load_supporting_files(root: Path, skill_name: str) -> list[str]:
    """Daftar berkas L3 (relatif) milik satu skill, terurut.

    Hanya berkas di dalam direktori skill itu sendiri; subdirektori ikut
    ditelusuri tetapi simlink dan path keluar direktori TIDAK diikuti —
    nama skill sudah divalidasi E01, dan fungsi ini menutup sisa jalurnya.
    """
    folder = root / skill_name
    if not folder.is_dir():
        return []
    base = folder.resolve()
    out: list[str] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if path.name == SKILL_FILENAME:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(base):  # simlink keluar direktori
            continue
        out.append(path.relative_to(folder).as_posix())
    return out
