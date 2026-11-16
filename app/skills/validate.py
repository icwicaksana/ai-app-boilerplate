"""Aturan validasi skill (E01–E08). Murni — tanpa I/O.

Alasan tiap aturan ada — semuanya mencegah kegagalan yang SENYAP:

  E01 name format      -> nama dipakai sebagai argumen tool; format bebas
                          memungkinkan path traversal (`../`) saat L3 dibaca.
  E02 description      -> satu-satunya yang dilihat model di indeks L1.
                          Tanpa ini skill tidak akan pernah dimuat.
  E03 when_to_use      -> tanpa ini model salah menilai kapan skill relevan;
                          salah satu penyebab tersering "skill tidak dipakai".
  E04 tags             -> dipakai filter & lint; format bebas = tidak bisa
                          difilter.
  E05 body non-kosong  -> skill tanpa isi = pemborosan satu slot indeks.
  E06 panjang batas    -> indeks dan body dibayar setiap giliran; tanpa batas
                          satu skill bisa menghabiskan seluruh konteks.
  E07 field tak dikenal-> typo (`descripion`) tidak boleh lolos diam-diam.
  E08 name == folder   -> dua sumber kebenaran nama = kebingungan saat debug.
"""

from __future__ import annotations

import re

from app.skills.model import Skill, SkillValidationError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

KNOWN_FIELDS = frozenset({"name", "description", "when_to_use", "tags"})

MIN_DESCRIPTION = 10
MAX_DESCRIPTION = 200
MIN_WHEN_TO_USE = 5
MAX_WHEN_TO_USE = 200
MAX_TAGS = 8
MIN_BODY = 1
MAX_BODY = 20_000


def _require_str(meta: dict, key: str, *, source: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillValidationError(f"{source}: '{key}' wajib diisi (string).")
    return value.strip()


def build_skill(meta: dict, body: str, *, source: str = "<string>") -> Skill:
    """Validasi meta+body lalu bentuk `Skill`. Raise SkillValidationError."""
    unknown = set(meta) - KNOWN_FIELDS
    if unknown:
        raise SkillValidationError(
            f"{source}: field tak dikenal {sorted(unknown)}; "
            f"yang sah: {sorted(KNOWN_FIELDS)}")

    name = _require_str(meta, "name", source=source)
    if not NAME_RE.match(name):
        raise SkillValidationError(
            f"{source}: 'name' harus huruf kecil/angka/dash, 2-63 karakter, "
            f"diawali huruf/angka (ditemukan {name!r}).")

    description = _require_str(meta, "description", source=source)
    if "\n" in description:
        raise SkillValidationError(
            f"{source}: 'description' harus satu baris (dipakai di indeks).")
    if not (MIN_DESCRIPTION <= len(description) <= MAX_DESCRIPTION):
        raise SkillValidationError(
            f"{source}: 'description' harus {MIN_DESCRIPTION}-{MAX_DESCRIPTION} "
            f"karakter (ditemukan {len(description)}).")

    when_to_use = _require_str(meta, "when_to_use", source=source)
    if "\n" in when_to_use:
        raise SkillValidationError(
            f"{source}: 'when_to_use' harus satu baris.")
    if not (MIN_WHEN_TO_USE <= len(when_to_use) <= MAX_WHEN_TO_USE):
        raise SkillValidationError(
            f"{source}: 'when_to_use' harus {MIN_WHEN_TO_USE}-{MAX_WHEN_TO_USE} "
            f"karakter (ditemukan {len(when_to_use)}).")

    raw_tags = meta.get("tags", [])
    if raw_tags is None:
        raw_tags = []
    if not isinstance(raw_tags, list):
        raise SkillValidationError(f"{source}: 'tags' harus berupa daftar.")
    tags: list[str] = []
    for tag in raw_tags:
        if not isinstance(tag, str) or not TAG_RE.match(tag):
            raise SkillValidationError(
                f"{source}: tag tidak sah {tag!r} (huruf kecil/angka/dash, "
                f"maks 32).")
        tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise SkillValidationError(
            f"{source}: maksimum {MAX_TAGS} tag (ditemukan {len(tags)}).")
    if len(set(tags)) != len(tags):
        raise SkillValidationError(f"{source}: tag duplikat.")

    body = (body or "").strip()
    if len(body) < MIN_BODY:
        raise SkillValidationError(f"{source}: body skill kosong.")
    if len(body) > MAX_BODY:
        raise SkillValidationError(
            f"{source}: body {len(body)} karakter melebihi batas {MAX_BODY}. "
            f"Pindahkan detail ke berkas L3 dan rujuk dari body.")

    return Skill(
        name=name,
        description=description,
        when_to_use=when_to_use,
        tags=tuple(sorted(tags)),
        body=body,
        source=source,
    )


def validate_folder_name(name: str, folder: str, *, source: str) -> None:
    """E08: nama di frontmatter harus sama dengan nama direktori."""
    if name != folder:
        raise SkillValidationError(
            f"{source}: 'name' ({name!r}) tidak sama dengan nama direktori "
            f"({folder!r}). Samakan supaya hanya ada satu sumber kebenaran.")
