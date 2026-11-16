"""Sidik jari skill. Murni — tanpa I/O.

Tanpa versioning manual (keputusan E09): `content_hash` per skill dan
`manifest_id` untuk seluruh roster memberi identitas yang berubah otomatis
saat isi berubah, sehingga deployment bisa dibandingkan tanpa disiplin
manusia.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.skills.model import Skill


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(skill: "Skill") -> str:
    """SHA-256 atas bentuk kanonis skill (meta + body).

    Kanonis = JSON dengan kunci terurut dan tag terurut, supaya:
      * urutan penulisan tag tidak mengubah sidik jari
      * perbedaan spasi di frontmatter tidak memicu "berubah"
    Body TIDAK dinormalkan isinya (spasi di dalam body bermakna).
    """
    payload = {
        "name": skill.name,
        "description": skill.description,
        "when_to_use": skill.when_to_use,
        "tags": sorted(skill.tags),
        "body": skill.body,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return _sha256(blob)


def manifest_id(skills: "list[Skill] | tuple[Skill, ...]") -> str:
    """SHA-256 atas roster: satu baris `name:hash` per skill, terurut nama.

    Urutan masukan tidak mempengaruhi hasil — dua deployment dengan skill
    sama tetapi urutan pemuatan berbeda tetap punya manifest_id sama.
    """
    lines = sorted(f"{s.name}:{s.content_hash}" for s in skills)
    return _sha256("\n".join(lines))
