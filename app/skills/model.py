"""Tipe data skill. Murni — tanpa I/O."""

from __future__ import annotations

from dataclasses import dataclass, field


class SkillError(Exception):
    """Kesalahan skill yang bisa ditampilkan ke operator."""


class SkillParseError(SkillError):
    """Berkas tidak bisa diurai (frontmatter hilang/rusak, YAML invalid)."""


class SkillValidationError(SkillError):
    """Berkas terurai tetapi melanggar aturan E01–E08."""


@dataclass(frozen=True)
class Skill:
    """Satu skill yang sudah divalidasi dan siap dipakai.

    `content_hash` dihitung dari body + meta kanonis, sehingga perubahan
    sekecil apa pun (termasuk urutan tag) mengubah sidik jarinya. Dipakai
    untuk mendeteksi skill yang berubah antar deployment tanpa versioning
    manual.
    """

    name: str
    description: str
    when_to_use: str
    tags: tuple[str, ...]
    body: str
    source: str = "<string>"
    content_hash: str = ""
    #: Nama berkas pendukung relatif terhadap direktori skill (L3).
    files: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.content_hash:
            from app.skills.hash import content_hash as _hash

            object.__setattr__(self, "content_hash", _hash(self))


@dataclass(frozen=True)
class SkillIndexEntry:
    """Satu baris indeks L1 yang selalu ada di system prompt.

    `when_to_use` ikut karena tanpanya model hanya melihat deskripsi dan
    sering salah menilai kapan skill relevan. Panjangnya dibatasi validator
    supaya indeks tidak membengkak.
    """

    name: str
    description: str
    when_to_use: str
