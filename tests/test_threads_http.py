"""Threads di level HTTP via TestClient (temuan F-18).

Checkpointer asli diganti fake via dependency_overrides — tanpa DB/docker.
Mengunci: 403-sebelum-404 via HTTP, bentuk state/history/fork/delete, 422.
"""

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, SystemMessage

from app.controllers.ThreadController import ThreadController
from app.controllers.runner import Runner
from app.core import dependencies as deps
from app.core.config import Settings
from app.infra.memory import PROFILE_MSG_ID
from tests._auth_helpers import app_with_settings, auth_headers


class _Tup:
    def __init__(self, tid, cid, messages, step=1, ts="2026-01-01T00:00:00"):
        self.config = {"configurable": {"thread_id": tid,
                                        "checkpoint_id": cid}}
        self.checkpoint = {"ts": ts,
                           "channel_values": {"messages": messages}}
        self.metadata = {"step": step, "source": "input"}


class _FakeCP:
    def __init__(self):
        self._t: dict = {}

    def seed(self, tid, cid, messages, ts="2026-01-01T00:00:00"):
        self._t.setdefault(tid, []).append(_Tup(tid, cid, messages, ts=ts))

    async def aget_tuple(self, config):
        c = config.get("configurable", {})
        items = self._t.get(c.get("thread_id"), [])
        cid = c.get("checkpoint_id")
        if cid:
            return next((t for t in items
                         if t.config["configurable"]["checkpoint_id"] == cid),
                        None)
        return items[-1] if items else None

    async def alist(self, config):
        for t in self._t.get(config.get("configurable", {}).get("thread_id"), []):
            yield t

    async def adelete_thread(self, tid):
        self._t.pop(tid, None)


def _settings():
    return Settings(_env_file=None, LITELLM_BASE_URL="http://localhost:4000",
                    LITELLM_API_KEY="x", LITELLM_MODEL="m")


def _client(cp):
    app = app_with_settings()
    app.dependency_overrides[deps.get_thread_controller] = \
        lambda: ThreadController(_settings(), cp, Runner())
    return TestClient(app, raise_server_exceptions=False)


def _hdr(user="budi", workspace="toko-a", rid="t1"):
    return auth_headers(workspace=workspace, user=user, request_id=rid)



TID = "toko-a:budi:sess-1"


def _seeded():
    cp = _FakeCP()
    cp.seed(TID, "c1", [HumanMessage(content="halo")],
            ts="2026-01-01T00:00:00")
    cp.seed(TID, "c2", [SystemMessage(content="nama: Budi",
                                      id=PROFILE_MSG_ID),
                        HumanMessage(content="halo"),
                        HumanMessage(content="hai")],
            ts="2026-01-02T00:00:00")
    return cp


def test_state_unknown_thread_404():
    res = _client(_FakeCP()).get(f"/api/v1/threads/{TID}/state", headers=_hdr())
    assert res.status_code == 404
    assert res.json()["code"] == "THREAD_NOT_FOUND"


def test_state_cross_user_403_before_404():
    c = _client(_seeded())
    res = c.get(f"/api/v1/threads/{TID}/state", headers=_hdr(user="ani"))
    assert res.status_code == 403
    assert res.json()["code"] == "CROSS_TENANT_DENIED"


def test_state_ok_hides_profile_and_counts_conversational():
    res = _client(_seeded()).get(f"/api/v1/threads/{TID}/state", headers=_hdr())
    assert res.status_code == 200
    body = res.json()
    assert body["checkpoint_id"] == "c2"  # tuple terbaru
    assert body["messages_count"] == 2  # profil disembunyikan
    assert all("Budi" not in m["content"] for m in body["messages"])


def test_state_last_n():
    res = _client(_seeded()).get(f"/api/v1/threads/{TID}/state?last_n=1",
                                 headers=_hdr())
    assert res.status_code == 200 and res.json()["messages_count"] == 1


def test_history_pagination_shape():
    res = _client(_seeded()).get(f"/api/v1/threads/{TID}/history?limit=1&offset=1",
                                 headers=_hdr())
    assert res.status_code == 200
    body = res.json()
    assert (body["total"], body["limit"], body["offset"]) == (2, 1, 1)
    assert len(body["items"]) == 1 and body["items"][0]["messages_count"] == 1


def test_delete_then_gone():
    c = _client(_seeded())
    assert c.delete(f"/api/v1/threads/{TID}", headers=_hdr()).status_code == 204
    assert c.get(f"/api/v1/threads/{TID}/state",
                 headers=_hdr()).status_code == 404
    assert c.delete(f"/api/v1/threads/{TID}",
                    headers=_hdr()).status_code == 404


def test_fork_blank_checkpoint_id_422_at_route():
    c = _client(_seeded())
    res = c.post(f"/api/v1/threads/{TID}/fork", headers=_hdr(),
                 json={"checkpoint_id": "", "agent": "example_chat"})
    assert res.status_code == 422


def test_fork_unknown_agent_404_before_io():
    c = _client(_seeded())
    res = c.post(f"/api/v1/threads/{TID}/fork", headers=_hdr(),
                 json={"checkpoint_id": "c1", "agent": "tidak_ada"})
    assert res.status_code == 404
    assert res.json()["code"] == "AGENT_NOT_FOUND"


def test_fork_ok_returns_canonical_new_thread(monkeypatch):
    from app.controllers import ThreadController as TC

    writes = []

    class _G:
        async def aupdate_state(self, config, values):
            writes.append((config, values))

    monkeypatch.setattr(TC.ThreadController, "_graph_for_fork",
                        lambda self, cls, nt: _G())
    c = _client(_seeded())
    res = c.post(f"/api/v1/threads/{TID}/fork", headers=_hdr(),
                 json={"checkpoint_id": "c1", "agent": "example_chat"})
    assert res.status_code == 200
    body = res.json()
    assert body["thread_id"].startswith("toko-a:budi:sess-")
    assert body["forked_from"] == "c1"
    assert writes and writes[0][0]["configurable"]["thread_id"].startswith(
        "toko-a:budi:sess-")
