"""Registry skill: dibangun sekali saat boot, hanya-baca sepanjang hidup proses.

KENAPA TIDAK HOT-RELOAD
    Skill hidup di system prompt (indeks L1) dan di-cache oleh prefix-cache
    LLM. Mengubahnya di tengah jalan akan (a) membuat prompt berubah tanpa
    versi, dan (b) membocorkan isi berbeda ke request yang sedang berjalan.
    Perubahan skill = deploy baru. Itu keputusan sadar, bukan keterbatasan.

Registry kosong (`empty()`) adalah keadaan normal ketika `SKILLS_ENABLED=false`
— dan pada keadaan itu perilaku sistem harus IDENTIK dengan v0.8.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.skills.hash import manifest_id as _manifest_id
from app.skills.index import build_index
from app.skills.model import Skill


@dataclass(frozen=True)
class SkillRegistry:
    """Kumpulan skill + daftar kesalahan pemuatan. Tidak bisa diubah."""

    skills: tuple[Skill, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    #: Akar direktori, disimpan untuk resolusi berkas L3.
    root: str | None = None

    # --- konstruksi -----------------------------------------------------

    @classmethod
    def empty(cls) -> "SkillRegistry":
        return cls()

    @classmethod
    def build(cls, skills: list[Skill], errors: list[str] | None = None,
              root: str | None = None) -> "SkillRegistry":
        # Urutkan di sini supaya SEMUA turunan (indeks, manifest) deterministik
        # tanpa bergantung pada urutan direktori di disk.
        ordered = tuple(sorted(skills, key=lambda s: s.name))
        return cls(skills=ordered, errors=tuple(errors or ()), root=root)

    # --- akses ----------------------------------------------------------

    def get(self, name: str) -> Skill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.skills)

    def index(self, *, include_when_to_use: bool = True,
              max_entries: int | None = None) -> str:
        return build_index(self.skills,
                           include_when_to_use=include_when_to_use,
                           max_entries=max_entries)

    @property
    def manifest_id(self) -> str:
        return _manifest_id(self.skills)

    def __len__(self) -> int:
        return len(self.skills)

    def __contains__(self, name: object) -> bool:
        return self.get(str(name)) is not None

    def __bool__(self) -> bool:
        # Registry kosong harus falsy supaya pemanggil bisa `if registry:`
        # tanpa memeriksa len() — dan `SKILLS_ENABLED=false` menghasilkan
        # objek yang memang "tidak ada".
        return bool(self.skills)
