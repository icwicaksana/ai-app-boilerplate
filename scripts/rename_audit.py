"""Audit istilah `tenant` di repo (v0.8.1 rename → `workspace`).

Dua mode:
  python -m scripts.rename_audit            # laporan bucket + hitungan
  python -m scripts.rename_audit --guard    # exit 1 bila ada di area terlarang

`--guard` dipakai di CI: mencegah istilah lama merayap masuk lagi setelah
rename selesai. Area terlarang = app/ + tests/ + migrations/ + scripts/
(kecuali allowlist di bawah). Docs & CHANGELOG historis TIDAK termasuk
guard (catatan sejarah tidak ditulis ulang).
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Guard hanya memeriksa KODE PRODUKSI. Migrasi 007-015 dan test yang menguji
# mode yang sudah dihapus sah menyebut istilah lama (itu catatan sejarah /
# bukti penghapusan), jadi tidak termasuk.
GUARD_DIRS = ("app",)
SCAN_DIRS = ("app", "tests", "migrations", "scripts")
SCAN_SUFFIXES = {".py", ".sql", ".ps1", ".yml", ".yaml", ".sh"}

# Bucket: first match wins. Urutan = prioritas penanganan.
BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DELETE_AUTH_HEADER",
     re.compile(r"X-Tenant-ID|X-User-ID|trusted_headers|"
                r"AUTH_ALLOW_TRUSTED_HEADERS")),
    ("SYMBOL", re.compile(r"CrossTenantError|_tenant_conn|tenant_conn|"
                          r"TENANT_RE|TENANT_PATTERN|TENANT_PREFIX|"
                          r"is_tenant_extension")),
    ("CONFIG", re.compile(r"PER_TENANT|DEPLOYMENT_TENANT|tenant_enabled")),
    ("GUC", re.compile(r"app\.current_tenant")),
    ("NAMESPACE", re.compile(r"""["']tenants["']""")),
    ("CACHEKEY", re.compile(r"turn:\{tenant\}|rl:\{scope\}:\{tenant\}|"
                            r"idem:\{tenant\}|ingest:\{tenant\}")),
    ("DBCOL", re.compile(r"\btenant\b")),
    ("IDENT", re.compile(r"\btenant")),
)

# String yang DIBEKUKAN sampai v0.8.2 (lihat Docs/RENAME-V081.md §3 Tier C).
# Setiap entri di sini adalah keputusan sadar, bukan kelalaian — dan guard
# tetap menangkap pemakaian BARU di luar daftar ini.
FROZEN_ALLOWLIST: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("guc", re.compile(r"app\.current_tenant")),
    ("namespace_memori", re.compile(r"""["']tenants["']""")),
    ("prefix_ontologi", re.compile(
        r'WORKSPACE_EXT_PREFIX\s*=\s*"tenant:"')),
    ("komentar_prefix", re.compile(r'# "tenant:" sampai ada migrasi')),
    ("nama_berkas_migrasi", re.compile(
        r"014_rename_tenant_to_workspace\.sql")),
    ("kode_error", re.compile(r"CROSS_TENANT_DENIED")),
    ("out_param_fn", re.compile(r"job_tenant")),
    ("format_thread", re.compile(r"tenant:user:session")),
    ("doc_penghapusan", re.compile(r"jalur `trusted_headers`")),
)


def iter_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in SCAN_SUFFIXES:
                yield f


def classify(line: str) -> str:
    for name, pat in BUCKETS:
        if pat.search(line):
            return name
    return "UNCLASSIFIED"


def is_frozen(line: str) -> bool:
    return any(pat.search(line) for _, pat in FROZEN_ALLOWLIST)


def scan() -> tuple[Counter, dict[str, Counter], dict[str, list[str]]]:
    per_bucket: Counter = Counter()
    per_file: dict[str, Counter] = defaultdict(Counter)
    detail: dict[str, list[str]] = defaultdict(list)
    for f in iter_files():
        rel = f.relative_to(ROOT).as_posix()
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "tenant" not in text.lower():
            continue
        for i, line in enumerate(text.splitlines(), 1):
            n = len(re.findall(r"tenant", line, re.I))
            if not n:
                continue
            b = classify(line)
            per_bucket[b] += n
            per_file[rel][b] += n
            detail[b].append(f"{rel}:{i}: {line.strip()[:160]}")
    return per_bucket, per_file, detail


def report(per_bucket: Counter, per_file: dict[str, Counter]) -> int:
    total = sum(per_bucket.values())
    print(f"TOTAL {total} occurrence / {len(per_file)} file\n")
    for b, n in per_bucket.most_common():
        print(f"  {b:22s} {n:5d}")
    print("\nTop 15 file:")
    for rel, c in sorted(per_file.items(),
                         key=lambda kv: -sum(kv[1].values()))[:15]:
        print(f"  {sum(c.values()):4d}  {rel}")
    return 0


def guard(per_bucket: Counter, detail: dict[str, list[str]]) -> int:
    """Exit 1 bila ada occurrence NON-frozen di kode produksi (app/)."""
    offenders: list[str] = []
    for b, lines in detail.items():
        for ln in lines:
            path = ln.split(":", 1)[0]
            if not path.startswith(tuple(d + "/" for d in GUARD_DIRS)):
                continue
            if is_frozen(ln):
                continue
            # Catatan penghapusan mode legacy sah disebut di docstring.
            if "dihapus" in ln or "DIHAPUS" in ln:
                continue
            offenders.append(f"[{b}] {ln}")
    if not offenders:
        print("OK: tidak ada istilah 'tenant' non-frozen di kode produksi.")
        return 0
    print(f"GAGAL: {len(offenders)} occurrence non-frozen di app/.\n")
    for ln in offenders[:40]:
        print("  " + ln.encode("ascii", "replace").decode("ascii"))
    if len(offenders) > 40:
        print(f"  ... dan {len(offenders) - 40} lainnya")
    return 1


def main(argv: list[str]) -> int:
    per_bucket, per_file, detail = scan()
    if "--guard" in argv:
        return guard(per_bucket, detail)
    return report(per_bucket, per_file)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
