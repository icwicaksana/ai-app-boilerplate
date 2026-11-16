"""Guard CI: istilah `tenant` tidak boleh kembali ke kode produksi (v0.8.1).

Mengapa ini ada: rename 1.806 occurrence tidak ada artinya kalau istilah lama
merayap masuk lagi di v0.9/v0.10. Test ini menjalankan `scripts.rename_audit
--guard` sehingga setiap PR yang menambahkan istilah lama di `app/` gagal.

Yang DIIZINKAN (didaftarkan eksplisit di FROZEN_ALLOWLIST, Tier C v0.8.2):
GUC `app.current_tenant`, namespace memori `"tenants"`, nilai prefix ontologi
`"tenant:"`, `job_tenant`, `CROSS_TENANT_DENIED`, `tenant:user:session`,
payload Qdrant `key="tenant"`.

Migrasi 007-015 dan test yang menguji mode yang sudah dihapus sengaja tidak
diperiksa: keduanya catatan sejarah / bukti penghapusan.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tidak_ada_istilah_tenant_di_kode_produksi():
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.rename_audit", "--guard"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, (
        "Istilah 'tenant' muncul lagi di app/ (non-frozen):\n"
        f"{proc.stdout}\n{proc.stderr}")
