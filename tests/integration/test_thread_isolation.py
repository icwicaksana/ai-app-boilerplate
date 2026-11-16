"""Isolasi workspace/user: 403 silang + namespace store terpisah (DB nyata)."""

import pytest

from app.controllers.ThreadController import ThreadController
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import CrossWorkspaceError
from app.core.threads import ThreadIdentity
from app.infra.memory import MemoryStore

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cross_workspace_reads_denied_200x(persistence, tag):
    _, checkpointer, store = persistence
    workspace_a, workspace_b, user = f"it-{tag}-a", f"it-{tag}-b", "budi"
    ctrl = ThreadController(Settings(_env_file=None), checkpointer, Runner())
    ident_a = ThreadIdentity(workspace=workspace_a, user=user)
    ident_b = ThreadIdentity(workspace=workspace_b, user=user)
    tid_a = f"{workspace_a}:{user}:sess-1"

    mem_a = MemoryStore(store, workspace_a, user)
    mem_b = MemoryStore(store, workspace_b, user)
    await mem_a.put_profile({"nama": "Si-A"})
    await mem_b.put_profile({"nama": "Si-B"})

    # 200 baca silang acak → semua 403, tanpa bocor
    denied = 0
    for i in range(200):
        ident, tid = (ident_b, tid_a) if i % 2 == 0 else (ident_a, tid_a.replace(workspace_a, workspace_b))
        try:
            await ctrl.get_state(ident, tid)
        except CrossWorkspaceError:
            denied += 1
    assert denied == 200

    # namespace store terpisah meski user sama
    assert (await mem_a.get_profile())["nama"] == "Si-A"
    assert (await mem_b.get_profile())["nama"] == "Si-B"

    # user berbeda, workspace sama → juga 403
    ident_c = ThreadIdentity(workspace=workspace_a, user="ani")
    with pytest.raises(CrossWorkspaceError):
        await ctrl.get_history(ident_c, tid_a)
