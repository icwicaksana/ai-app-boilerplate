"""Scaffold subagent baru: `python -m scripts.new_subagent <nama>`.

Menulis `app/agents/subagents/<modul>.py` berisi SubagentSpec + register().
Hasil SELALU di-compile sebelum ditulis. Pendaftaran terjadi saat modul
di-import — 1 baris import di composition root fork (lihat cetakan modul).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.agents.subagents.registry import SPEC_NAME_RE  # noqa: E402

_MODULE_TEMPLATE = '''"""Subagent {name} (fork). Didaftarkan saat modul di-import.

Aktifkan: tambah 1 baris di composition root fork-mu:
    from app.agents.subagents import {module}  # noqa: F401
"""

from __future__ import annotations

from app.agents.subagents.registry import SubagentSpec, register

register(SubagentSpec(
    name="{name}",
    description="[ISI 1 BARIS: apa yang dikerjakan pekerja ini]",
    when_to_use="[ISI: kapan agen utama harus mendelegasikan ke sini]",
    system_prompt=(
        "[ISI: peran + aturan kerja pekerja. Ingat: ia TIDAK menerima "
        "riwayat percakapan — hanya Brief. Ia wajib lapor jujur bila data "
        "tak didapat.]"
    ),
    # NAMA tool yang boleh dipakai pekerja (subset dari build induk).
    # DILARANG: tool approval-gated (HITL_POLICY) dan tool delegasi lain.
    tools=("[ISI_NAMA_TOOL]",),
    max_tokens=8_000,
    timeout_s=45,
    thread_limit=10,
))
'''


def module_name_for(spec_name: str) -> str:
    """Nama modul Python dari nama spec (dash → underscore)."""
    return spec_name.replace("-", "_").lower()


def render_subagent_module(name: str) -> tuple[str, str]:
    """Kembalikan (nama_modul, isi_modul). Raise ValueError bila tak valid."""
    if not SPEC_NAME_RE.match(name):
        raise ValueError(
            f"nama subagent tak valid: {name!r} "
            f"(huruf/angka/dash/underscore, maks 63).")
    module = module_name_for(name)
    if not module.isidentifier():
        raise ValueError(f"nama {name!r} tak bisa jadi modul Python.")
    rendered = _MODULE_TEMPLATE.format(name=name, module=module)
    compile(rendered, f"<{module}>", "exec")  # jaminan: selalu sintaks valid
    return module, rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buat subagent baru dari template.")
    parser.add_argument("name", help="nama spec (mis. cek-pesanan)")
    args = parser.parse_args(argv)

    try:
        module, rendered = render_subagent_module(args.name)
    except ValueError as exc:
        print(f"GAGAL: {exc}", file=sys.stderr)
        return 2

    target = REPO_ROOT / "app" / "agents" / "subagents" / f"{module}.py"
    if target.exists():
        print(f"GAGAL: {target} sudah ada — pilih nama lain.", file=sys.stderr)
        return 2
    target.write_text(rendered, encoding="utf-8")
    print(f"OK: {target}")
    print("Langkah berikut:")
    print(f"  1. Isi [ISI ...] di modul itu (tools = subset build induk).")
    print(f"  2. Import sekali dari composition root fork-mu.")
    print(f"  3. python -m scripts.skills_lint   # validasi roster")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
