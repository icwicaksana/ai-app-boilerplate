"""Indeks L1 skill. Murni — tanpa I/O.

Indeks ini hidup di system prompt dan dibayar SETIAP giliran, jadi dua
propertinya penting:

1. **Byte-stabil.** Urutan selalu alfabetis, tanpa timestamp, tanpa hitungan
   dinamis. Kalau indeks berubah tiap request, prefix-cache LLM gugur dan
   seluruh system prompt dibayar ulang.

2. **Sekecil mungkin, tapi cukup untuk memutuskan.** `description` saja
   membuat model sering salah menilai kapan skill relevan; `when_to_use`
   menolong tetapi menggandakan biaya per entri. Karena itu bisa dimatikan
   lewat `include_when_to_use` ketika jumlah skill bertambah.

Ambang kapan indeks harus keluar dari prompt (router) ada di docs/V09-PLAN.md §7.
"""

from __future__ import annotations

from typing import Iterable

from app.skills.model import Skill


def build_index(
    skills: Iterable[Skill],
    *,
    include_when_to_use: bool = True,
    max_entries: int | None = None,
) -> str:
    """Bangun indeks L1 deterministik. Kosong bila tidak ada skill."""
    ordered = sorted(skills, key=lambda s: s.name)
    if max_entries is not None:
        ordered = ordered[:max_entries]

    lines: list[str] = []
    for skill in ordered:
        if include_when_to_use:
            lines.append(
                f"- {skill.name}: {skill.description} "
                f"(pakai saat: {skill.when_to_use})")
        else:
            lines.append(f"- {skill.name}: {skill.description}")
    return "\n".join(lines)


def index_entries(skills: Iterable[Skill]) -> list[tuple[str, str, str]]:
    """Bentuk terstruktur dari indeks — untuk health/lint, bukan prompt."""
    return [
        (s.name, s.description, s.when_to_use)
        for s in sorted(skills, key=lambda s: s.name)
    ]
