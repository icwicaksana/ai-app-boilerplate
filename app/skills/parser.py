"""Parser berkas skill. Murni — tanpa I/O.

Memisahkan dua jenis kegagalan, karena penanganannya berbeda:
  * `SkillParseError`      — berkas rusak (frontmatter/YAML). Operator salah
                             menulis; tolak dengan pesan jelas.
  * `SkillValidationError` — berkas valid secara sintaks tetapi melanggar
                             kontrak. Operator lupa/menyingkat isi.
"""

from __future__ import annotations

import re

import yaml

from app.skills.model import Skill, SkillParseError
from app.skills.validate import MAX_BODY, build_skill

#: `---` pembuka, isi, `---` penutup, di awal berkas.
#: `\A` dipakai (bukan `^`) supaya frontmatter HARUS di baris pertama.
FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def parse_skill(text: str, *, source: str = "<string>",
                max_body: int = MAX_BODY) -> Skill:
    """Urai teks berkas skill menjadi `Skill` tervalidasi."""
    if text.startswith("\ufeff"):
        # Pertahanan lapis kedua: loader membaca dengan utf-8-sig, tetapi
        # pemanggil langsung (tes, tooling) mungkin tidak.
        text = text.lstrip("\ufeff")
    if not text or not text.strip():
        raise SkillParseError(f"{source}: berkas kosong")

    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise SkillParseError(
            f"{source}: frontmatter tidak ditemukan. Berkas harus dimulai "
            f"dengan '---' pada baris pertama, diikuti blok YAML, lalu '---'.")

    try:
        meta = yaml.safe_load(match.group("meta"))
    except yaml.YAMLError as exc:
        raise SkillParseError(f"{source}: YAML frontmatter tidak valid: {exc}") from exc

    if not isinstance(meta, dict):
        raise SkillParseError(
            f"{source}: frontmatter harus berupa pemetaan kunci-nilai "
            f"(ditemukan {type(meta).__name__}).")

    body = text[match.end():].strip()
    return build_skill(meta, body, source=source, max_body=max_body)
