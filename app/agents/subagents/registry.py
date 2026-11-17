"""Roster subagent: STATIS di kode (K1).

LLM memutuskan KAPAN mendelegasikan, KE SIAPA, dan isi Brief — tetapi
TIDAK PERNAH menentukan tool apa yang dimiliki pekerja, middleware apa
yang menjaganya, atau menaikkan budget. Alasannya keamanan: bila LLM bisa
menentukan tool set, ia bisa membangun pekerja ber-tool tulis TANPA
middleware HITL dan melewati approval.

Menambah subagent = tambah 1 SubagentSpec di sini (atau modul fork) + daftarkan
nama di SUBAGENTS. Pola registry datar seperti ToolRegistry.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SubagentSpec:
    """Definisi satu subagent. Semua batas = batas ATAS (LLM boleh turunkan)."""

    name: str                    # [a-z0-9-], dipakai di tool subagent__<name>
    description: str             # 1 baris untuk indeks delegasi
    when_to_use: str             # kapan main agent harus mendelegasikan
    system_prompt: str           # prompt pekerja (konteks terpisah)
    tools: tuple[str, ...] = ()  # NAMA tool, di-resolve saat build
    max_tokens: int = 8_000      # anggaran token run ini
    timeout_s: int = 45          # wall-clock run ini
    thread_limit: int = 10       # rem tool-call dalam run ini


SUBAGENTS: dict[str, SubagentSpec] = {}


def register(spec: SubagentSpec) -> SubagentSpec:
    """Daftarkan spec. Nama duplikat = ValueError (fail-fast saat import)."""
    if spec.name in SUBAGENTS:
        raise ValueError(f"subagent '{spec.name}' sudah terdaftar.")
    SUBAGENTS[spec.name] = spec
    return spec


def get_spec(name: str) -> SubagentSpec | None:
    return SUBAGENTS.get(name)


def delegation_index() -> str:
    """Indeks delegasi untuk system prompt (byte-stabil, alfabetis)."""
    lines = [
        f"- {s.name}: {s.description} (pakai saat: {s.when_to_use})"
        for s in sorted(SUBAGENTS.values(), key=lambda s: s.name)
    ]
    return "\n".join(lines)
