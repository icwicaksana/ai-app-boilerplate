"""Isolasi tenant/user: 403 silang + namespace store terpisah (DB nyata)."""

import pytest

from app.controllers.ThreadController import ThreadController
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import CrossTenantError
from app.core.threads import ThreadIdentity
from app.infra.memory import MemoryStore

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cross_tenant_reads_denied_200x(persistence, tag):
    _, checkpointer, store = persistence
    tenant_a, tenant_b, user = f"it-{tag}-a", f"it-{tag}-b", "budi"
    ctrl = ThreadController(Settings(_env_file=None), checkpointer, Runner())
    ident_a = ThreadIdentity(tenant=tenant_a, user=user)
    ident_b = ThreadIdentity(tenant=tenant_b, user=user)
    tid_a = f"{tenant_a}:{user}:sess-1"

    mem_a = MemoryStore(store, tenant_a, user)
    mem_b = MemoryStore(store, tenant_b, user)
    await mem_a.put_profile({"nama": "Si-A"})
    await mem_b.put_profile({"nama": "Si-B"})

    # 200 baca silang acak → semua 403, tanpa bocor
    denied = 0
    for i in range(200):
        ident, tid = (ident_b, tid_a) if i % 2 == 0 else (ident_a, tid_a.replace(tenant_a, tenant_b))
        try:
            await ctrl.get_state(ident, tid)
        except CrossTenantError:
            denied += 1
    assert denied == 200

    # namespace store terpisah meski user sama
    assert (await mem_a.get_profile())["nama"] == "Si-A"
    assert (await mem_b.get_profile())["nama"] == "Si-B"

    # user berbeda, tenant sama → juga 403
    ident_c = ThreadIdentity(tenant=tenant_a, user="ani")
    with pytest.raises(CrossTenantError):
        await ctrl.get_history(ident_c, tid_a)
