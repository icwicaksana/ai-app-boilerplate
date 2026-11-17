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

KEAMANAN (hasil review QA F2):
    * Ukuran berkas dicek SEBELUM dibaca — `SKILL.md` 1 GB tidak boleh OOM
      sebelum validator sempat menolaknya.
    * `UnicodeError` diperlakukan seperti `OSError`: satu skill ber-encoding
      salah menjadi entri error, bukan boot yang mati total.
    * Symlink keluar direktori ditolak di DUA jalur (SKILL.md dan L3), dan
      nama skill divalidasi E01 sebelum dipakai sebagai path.
    * BOM UTF-8 (Notepad Windows) ditoleransi saat baca.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.skills.model import Skill, SkillError
from app.skills.parser import parse_skill
from app.skills.validate import MAX_BODY, NAME_RE, validate_folder_name

SKILL_FILENAME = "SKILL.md"
IGNORED_PREFIX = "_"

#: Batas baca per berkas SEBELUM parsing. Jauh di atas MAX_BODY validator
#: (20.000) supaya berkas raksasa ditolak murah tanpa OOM.
MAX_FILE_BYTES = 100_000


def discover(root: Path) -> list[Path]:
    """Direktori skill yang sah, terurut alfabetis (deterministik)."""
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir()
         if p.is_dir() and not p.name.startswith(IGNORED_PREFIX)),
        key=lambda p: p.name,
    )


def _within(child: Path, parent: Path) -> bool:
    """True bila `child` ter-resolve masih di dalam `parent` ter-resolve."""
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def load_skills(
    root: Path,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_body_chars: int = MAX_BODY,
) -> tuple[list[Skill], list[str]]:
    """Muat semua skill di `root`. Tidak pernah raise untuk skill rusak."""
    skills: list[Skill] = []
    errors: list[str] = []

    for folder in discover(root):
        if not _within(folder, root):
            errors.append(
                f"{folder.name}: ditolak — direktori keluar dari root skill "
                f"(symlink nakal?)")
            continue
        path = folder / SKILL_FILENAME
        if not path.is_file():
            errors.append(
                f"{folder.name}: tidak ada {SKILL_FILENAME} "
                f"(setiap direktori skill wajib punya berkas ini)")
            continue
        if not _within(path, folder):
            errors.append(
                f"{folder.name}: ditolak — {SKILL_FILENAME} keluar dari "
                f"direktorinya (symlink nakal?)")
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            errors.append(f"{folder.name}: gagal membaca {path.name}: {exc}")
            continue
        if size > max_file_bytes:
            errors.append(
                f"{folder.name}: {path.name} {size} byte melebihi batas baca "
                f"{max_file_bytes} — perkecil atau pecah ke berkas L3")
            continue
        try:
            # utf-8-sig: toleransi BOM Notepad Windows.
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{folder.name}: gagal membaca {path.name}: {exc}")
            continue
        try:
            skill = parse_skill(text, source=f"{folder.name}/{SKILL_FILENAME}",
                                max_body=max_body_chars)
            validate_folder_name(skill.name, folder.name, source=folder.name)
        except SkillError as exc:
            errors.append(str(exc))
            continue
        skills.append(replace(
            skill, files=tuple(_supporting_files_in(folder))))

    return skills, errors


def _supporting_files_in(folder: Path) -> list[str]:
    """Daftar berkas L3 milik `folder` (relatif, terurut, tanpa SKILL.md)."""
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


def load_supporting_files(root: Path, skill_name: str) -> list[str]:
    """Daftar berkas L3 (relatif) milik satu skill, terurut.

    Fail-closed: nama tak tervalidasi atau folder di luar root → daftar
    kosong, bukan exception. F3 (`read_skill_file`) memvalidasi ulang dan
    mengembalikan pesan panduan saat baca, karena TOCTOU symlink-swap antara
    list dan read tetap mungkin.
    """
    if not NAME_RE.match(skill_name):
        return []
    folder = root / skill_name
    if not folder.is_dir():
        return []
    if not _within(folder, root):
        return []
    return _supporting_files_in(folder)
