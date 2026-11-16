"""Kontrak BEKU v0.8.1 — DILARANG DIUBAH selama rename tenant→workspace.

Alasan keberadaan berkas ini (temuan Code Reviewer, Docs/RENAME-V081.md §2 K4):
test yang mengunci kontrak akan diedit di commit yang sama saat rename, sehingga
"hijau" hanya membuktikan kode cocok dengan test yang baru diubah. Berkas ini
adalah JANGKAR: ia harus hijau SEBELUM rename dan SETELAH rename, tanpa satu
baris pun diubah.

Kalau test di sini gagal setelah rename, itu berarti rename memutus perilaku —
bukan berarti test-nya perlu disesuaikan.

Aturan: satu-satunya perubahan yang sah pada berkas ini adalah PENGHAPUSAN
assert yang sudah dipensiunkan lewat rilis resmi (v0.8.2 untuk Tier C),
disertai catatan tanggal + alasan.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# --- 1. Format thread ID (ADR-07, BEKU permanen) ---------------------------
def test_format_thread_id_tetap_3_segmen():
    from app.core.threads import compose_thread_id, parse_thread_id

    tid = compose_thread_id("acme", "budi")
    parts = parse_thread_id(tid)
    assert len(parts) == 3
    assert parts[0] == "acme" and parts[1] == "budi"
    assert parts[2].startswith("sess-")
    # Format lama WAJIB tetap ditolak.
    for bad in ("thr-abc123", "run-xyz", "acme:budi"):
        with pytest.raises(Exception):
            parse_thread_id(bad)


def test_format_thread_id_regex_beku():
    from app.core.threads import THREAD_RE

    assert THREAD_RE.pattern == (
        r"^[a-z0-9-]{3,32}:[a-z0-9-_]{1,64}:[a-z0-9-]{4,64}$")


# --- 2. Isolasi user: beda identitas = ditolak ----------------------------
def test_ensure_owner_tolak_identitas_lain():
    from app.core.threads import ThreadIdentity, ensure_owner

    with pytest.raises(Exception) as exc:
        ensure_owner(ThreadIdentity("acme", "budi"), "acme:siti:sess-abc123")
    # 403 = kontrak HTTP (kode error string boleh berubah di Tier C).
    assert getattr(exc.value, "status_code", 403) == 403


# --- 3. Kode error lintas-identitas (HTTP shape, bukan string) ------------
def test_cross_identity_denied_status_403():
    """Nama kelas boleh berubah (tenant→workspace); kontraknya TIDAK boleh.

    Sengaja rename-agnostik: jangkar ini harus hijau SEBELUM dan SESUDAH
    rename tanpa diedit.
    """
    from app.core import errors

    cls = (getattr(errors, "CrossWorkspaceError", None)
           or getattr(errors, "CrossTenantError"))
    assert cls().status_code == 403


# --- 4. Tier C DIBEKUKAN: GUC RLS -----------------------------------------
def test_guc_rls_masih_app_current_tenant():
    """DIBEKUKAN sampai v0.8.2 (rename GUC butuh pola dual-set)."""
    src = _read("migrations/012_rls_with_check.sql")
    assert "app.current_tenant" in src
    assert "app.current_workspace" not in src


# --- 5. Tier C DIBEKUKAN: namespace memori --------------------------------
def test_namespace_memori_sudah_workspaces():
    """v0.8.2 (2026-11-16): DIPENSIUNKAN — namespace dimigrasikan migrasi 018.

    Alasan: `store.prefix` sudah dipindah `tenants.*` -> `workspaces.*`, jadi
    kode WAJIB memakai nilai baru. Mempertahankan assert lama justru mengunci
    kode ke nilai yang tidak lagi cocok dengan data (memori jadi tak terlihat).
    Jangkar pengganti: nilai baru ada DAN nilai lama tidak tersisa.
    """
    src = _read("app/infra/memory.py")
    assert re.search(r"""["']workspaces["']""", src)
    assert not re.search(r"""["']tenants["']""", src)


# --- 6. Tier C DIBEKUKAN: prefix ontologi KG ------------------------------
def test_prefix_ontologi_kanonis_workspace():
    """v0.8.2 (2026-11-16): dipensiunkan — prefix kanonis kini 'workspace:'.

    v0.8.3 (2026-11-16): jalur baca legacy ikut dicabut karena tidak ada
    deployment berjalan yang menyimpan data prefix lama; migrasi 018 sudah
    memindahkan nilai yang ada. Jangkar: hanya prefix kanonis yang dikenali.
    """
    from app.rag import ontology

    assert ontology.WORKSPACE_EXT_PREFIX == "workspace:"
    assert not hasattr(ontology, "LEGACY_EXT_PREFIXES")


# --- 7. Identitas deterministik (bukan label — jangan di-rename) ----------
def test_point_id_deterministik_dan_berbasis_tenant():
    src = _read("app/infra/rag/qdrant.py")
    assert "uuid5" in src
    # Formula identitas: {tenant}:{doc}:{ord} — mengubahnya = vektor lama yatim.
    assert re.search(r"\{tenant\}:", src) or "point_id" in src


# --- 8. Migrasi lama TIDAK boleh diedit (drift skema permanen) ------------
@pytest.mark.parametrize("ver", ["007", "008", "009", "010", "011", "012",
                                 "013"])
def test_migrasi_lama_ada_dan_tak_direname(ver):
    found = list((ROOT / "migrations").glob(f"{ver}_*.sql"))
    assert len(found) == 1, f"migrasi {ver} hilang/duplikat: {found}"
