"""Test alat rename tenant→workspace (scripts/rename_tenant.py).

Mengunci perilaku alat: token Tier C (beku) TIDAK boleh tersentuh, identifier
Tier A harus berubah. Kalau test ini gagal, jangan jalankan --apply.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.rename_tenant import transform  # noqa: E402


@pytest.mark.parametrize("frozen", [
    "app.current_tenant",
    'query "tenants"',
    "TENANT_PREFIX",
    'prefix "tenant:"',
    "job_tenant",
    "CROSS_TENANT_DENIED",
    "tenant:user:session",
    'key="tenant"',
    "X-Tenant-ID",
    "X-User-ID",
    "AUTH_MODE=trusted_headers",
])
def test_token_beku_tidak_tersentuh(frozen: str):
    out = transform(frozen)
    assert out == frozen, f"token beku berubah: {out!r}"


@pytest.mark.parametrize(("src", "want"), [
    ("tenant: str", "workspace: str"),
    ("self._tenant", "self._workspace"),
    ("CrossTenantError()", "CrossWorkspaceError()"),
    ("retriever_for_tenant(", "retriever_for_workspace("),
    ("_all_tenants(pool)", "_all_workspaces(pool)"),
    ("TENANT_RE", "WORKSPACE_RE"),
    ("GRAPH_QUOTA_EDGES_PER_TENANT", "GRAPH_QUOTA_EDGES_PER_WORKSPACE"),
    ("tenant_id=1", "workspace_id=1"),
])
def test_identifier_tier_a_diubah(src: str, want: str):
    assert transform(src) == want


def test_baris_campuran_beku_dan_identifier():
    """Satu baris memuat token beku DAN identifier → hanya identifier berubah."""
    src = ("async def _tenant_conn(pool, tenant):\n"
           '    await conn.execute("SELECT set_config('
           "'app.current_tenant', %s, true)\", (tenant,))")
    out = transform(src)
    assert "_workspace_conn" in out          # identifier berubah
    assert "app.current_tenant" in out       # GUC utuh
    assert "workspace_conn(pool, workspace)" in out


def test_tidak_ada_perubahan_ganda():
    """`tenants` tidak boleh jadi `workspaces` lewat dua langkah salah."""
    assert transform("tenants") == "workspaces"
    assert transform("workspace") == "workspace"  # idempoten
    once = transform("tenant")
    assert transform(once) == once                # idempoten
