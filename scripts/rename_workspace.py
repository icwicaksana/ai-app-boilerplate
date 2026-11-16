"""Rename istilah tenant→workspace (v0.8.1, Tier A) — aman & dapat diaudit.

Prinsip:
1. Token BEKU dilindungi lebih dulu lewat placeholder, jadi rename tidak bisa
   menyentuhnya walau berada di baris yang sama dengan identifier biasa
   (contoh: `_tenant_conn` + `set_config('app.current_tenant', ...)`).
2. Aturan berurutan longest-first agar `tenant_id` tidak jadi `workspace_id`
   lewat dua langkah yang salah.
3. Dry-run default; `--apply` untuk menulis. Setiap perubahan dicatat.

Pemakaian:
  python -m scripts.rename_tenant --tier a            # dry-run
  python -m scripts.rename_tenant --tier a --apply
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- Token yang DIBEKUKAN sampai v0.8.2 (Tier C) --------------------------
FROZEN: tuple[str, ...] = (
    "app.current_tenant",
    '"tenants"',
    "'tenants'",
    "TENANT_PREFIX",          # simbol + nilai "tenant:" (data tersimpan)
    '"tenant:"',
    "job_tenant",             # OUT param fungsi SECURITY DEFINER
    "CROSS_TENANT_DENIED",    # kode error API (kontrak FE)
    "tenant:user:session",    # format thread (ADR-07)
    'key="tenant"',           # payload Qdrant
    # Header mode auth dev: DIHAPUS di F5, bukan di-rename. Rename di sini
    # akan membuatnya tampak seperti kontrak baru yang sah.
    "X-Tenant-ID",
    "X-User-ID",
    "trusted_headers",
    "AUTH_ALLOW_TRUSTED_HEADERS",
)

# --- Aturan rename Tier A -------------------------------------------------
# PENTING: penggantian dilakukan sebagai substring berurutan, TERPANJANG
# DULU. Batas-kata regex TIDAK dipakai karena `_` dianggap karakter kata,
# sehingga `PER_TENANT` di dalam `GRAPH_QUOTA_EDGES_PER_TENANT` tidak akan
# pernah cocok (bug tertangkap tests/test_rename_tool.py sebelum apply).
# Keamanan datang dari urutan + proteksi token beku, bukan dari batas kata.
RULES: tuple[tuple[str, str], ...] = (
    ("retriever_for_tenant", "retriever_for_workspace"),
    ("is_tenant_extension", "is_workspace_extension"),
    ("CrossTenantError", "CrossWorkspaceError"),
    ("DEPLOYMENT_TENANT", "DEPLOYMENT_WORKSPACE"),
    ("reindex_tenant", "reindex_workspace"),
    ("TENANT_PATTERN", "WORKSPACE_PATTERN"),
    ("tenant_enabled", "workspace_enabled"),
    ("_all_tenants", "_all_workspaces"),
    ("_tenant_conn", "_workspace_conn"),
    ("cross_tenant", "cross_workspace"),
    ("tenant_conn", "workspace_conn"),
    ("PER_TENANT", "PER_WORKSPACE"),
    ("_tenants", "_workspaces"),
    ("TENANT_RE", "WORKSPACE_RE"),
    ("tenant_id", "workspace_id"),
    ("_tenant", "_workspace"),
    ("tenants", "workspaces"),
    ("Tenant", "Workspace"),
    ("tenant", "workspace"),
    ("TENANT", "WORKSPACE"),
)

TIER_A_DIRS = ("app", "tests")
TIER_A_SUFFIXES = {".py"}
# Jangkar kontrak beku: DILARANG disentuh oleh alat rename (lihat
# Docs/RENAME-V081.md §2 K4). Ia harus hijau sebelum & sesudah rename.
TIER_A_SKIP = {"tests/test_frozen_contract.py"}


def _protect(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def repl(m: re.Match[str]) -> str:
        saved.append(m.group(0))
        return f"\x00F{len(saved) - 1}\x00"

    for tok in FROZEN:
        text = text.replace(tok, f"\x00S{len(saved)}\x00") if False else text
    # replace terurut panjang-desc agar token panjang menang
    for tok in sorted(FROZEN, key=len, reverse=True):
        text = re.sub(re.escape(tok), repl, text)
    return text, saved


def _restore(text: str, saved: list[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        return saved[int(m.group(1))]

    return re.sub(r"\x00F(\d+)\x00", repl, text)


def _rename(text: str) -> str:
    """Substring berurutan, terpanjang dulu (lihat catatan RULES)."""
    for old, new in sorted(RULES, key=lambda kv: -len(kv[0])):
        text = text.replace(old, new)
    return text


def transform(text: str) -> str:
    protected, saved = _protect(text)
    renamed = _rename(protected)
    return _restore(renamed, saved)


def iter_files():
    for d in TIER_A_DIRS:
        base = ROOT / d
        for f in base.rglob("*"):
            if not (f.is_file() and f.suffix in TIER_A_SUFFIXES):
                continue
            rel = f.relative_to(ROOT).as_posix()
            if rel in TIER_A_SKIP:
                continue
            yield f


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    changed: list[tuple[str, int]] = []
    for f in iter_files():
        try:
            src = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "tenant" not in src.lower():
            continue
        out = transform(src)
        if out == src:
            continue
        n = sum(1 for a, b in zip(src.splitlines(), out.splitlines())
                if a != b)
        changed.append((f.relative_to(ROOT).as_posix(), n))
        if apply:
            f.write_text(out, encoding="utf-8")
    print(("APPLIED" if apply else "DRY-RUN") +
          f": {len(changed)} file, {sum(n for _, n in changed)} baris")
    for rel, n in sorted(changed, key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
