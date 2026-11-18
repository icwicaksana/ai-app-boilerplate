"""ApprovalService (v0.6): rekam interrupt → keputusan → resume graph.

Alur produksi:
1. Streaming berhenti karena interrupt (`result` dengan `interrupts`).
2. Route memanggil `record_interrupts()` → baris `approvals` (pending) + audit
   `approval_requested`.
3. Reviewer memutuskan lewat API → `decide()`:
   - klaim atomik (anti double-approve),
   - **TOCTOU guard**: hash aksi saat ini harus sama dengan yang disetujui,
   - resume graph dengan `Command(resume={"decisions": [...]})`,
   - audit `approval_decided` (+ hasil resume).
4. Bila resume memunculkan interrupt baru → dicatat lagi (rantai approval).

Catatan v0.6: keputusan berlaku untuk SEMUA aksi dalam satu interrupt
(mode `batched`); keputusan campuran per-aksi belum didukung.
"""

from __future__ import annotations

from typing import Any

from app.agents.AgentContext import AgentContext
from app.agents.BaseChatAgent import BaseChatAgent
from app.controllers.registry import CHAT_AGENTS
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import AgentNotFoundError, AppError, ForbiddenError
from app.core.hitl import action_hash, policy_for, policy_version
from app.core.logging import get_logger
from app.infra import approvals as repo

log = get_logger(__name__)


class ApprovalNotFoundError(AppError):
    def __init__(self, approval_id: int):
        super().__init__(f"Approval {approval_id} tidak ditemukan.",
                         status_code=404, code="APPROVAL_NOT_FOUND",
                         title="Approval Not Found")


class ApprovalStateError(AppError):
    def __init__(self, status: str):
        super().__init__(f"Approval sudah berstatus '{status}'.",
                         status_code=409, code="APPROVAL_ALREADY_DECIDED",
                         title="Approval Already Decided")


class ApprovalStaleError(AppError):
    """Aksi berubah antara persetujuan dan eksekusi (TOCTOU)."""

    def __init__(self):
        super().__init__(
            "Aksi berubah sejak approval diminta — minta persetujuan ulang.",
            status_code=409, code="APPROVAL_STALE", title="Approval Stale")


class ApprovalExpiredError(AppError):
    def __init__(self):
        super().__init__("Approval kedaluwarsa — minta persetujuan ulang.",
                         status_code=409, code="APPROVAL_EXPIRED",
                         title="Approval Expired")


class ApprovalMultiInterruptError(AppError):
    """Thread punya >1 approval pending (v1.0 R8)."""

    def __init__(self, count: int):
        super().__init__(
            f"Thread punya {count} approval pending — putuskan satu per satu "
            f"setelah interrupt lain selesai (multi-interrupt tak didukung).",
            status_code=409, code="APPROVAL_MULTI_INTERRUPT",
            title="Multiple Pending Approvals")


class ApprovalService:
    def __init__(self, settings: Settings, pool, checkpointer,
                 runner: Runner | None = None, *, store=None, rag=None,
                 skills=None):
        self.settings = settings
        self.pool = pool
        self.checkpointer = checkpointer
        self.runner = runner or Runner(durability=settings.GRAPH_DURABILITY)
        # v0.9 F0-5: dipakai _graph_for agar graph yang dibangun ulang saat
        # resume identik dengan graph yang di-interrupt (skema tool sama).
        self.store = store
        self.rag = rag
        self.skills = skills

    # --- perekaman ---
    async def record_interrupts(self, *, workspace: str, thread_id: str,
                                agent: str, interrupts: list[dict[str, Any]],
                                request_id: str = "-") -> list[dict[str, Any]]:
        """Simpan setiap interrupt sebagai approval pending (idempotent)."""
        out: list[dict[str, Any]] = []
        for item in interrupts or []:
            snapshot = item.get("value") if isinstance(item.get("value"), dict) else {}
            actions = snapshot.get("action_requests") or []
            tool_name = ",".join(str(a.get("name", "?")) for a in actions) or "unknown"
            expires = 86_400
            pol = policy_for(str(actions[0].get("name"))) if actions else None
            if pol is not None:
                expires = pol.expires_in_seconds
            row, created = await repo.record_pending(
                self.pool, workspace=workspace, thread_id=thread_id,
                interrupt_id=str(item.get("id", "")), agent=agent,
                tool_name=tool_name,
                action_hash=action_hash(tool_name, snapshot),
                action_snapshot=snapshot,
                policy_version=policy_version(),
                expires_in_seconds=expires)
            if row:
                out.append(row)
                if not created:
                    continue  # sudah ada: jangan audit ganda (R7)
                await repo.append_audit(
                    self.pool, workspace=workspace, actor="system",
                    action="approval_requested", subject_type="approval",
                    subject_id=str(row["id"]),
                    payload={"thread_id": thread_id, "agent": agent,
                             "tool": tool_name,
                             "policy_version": policy_version()},
                    request_id=request_id)
        return out

    # --- pembacaan ---
    async def list(self, *, workspace: str, status: str | None = "pending",
                   limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        await repo.expire_stale(self.pool, workspace=workspace)
        return await repo.list_approvals(self.pool, workspace=workspace, status=status,
                                         limit=limit, offset=offset)

    async def get(self, *, workspace: str, approval_id: int) -> dict[str, Any]:
        await repo.expire_stale(self.pool, workspace=workspace)
        row = await repo.get_approval(self.pool, workspace=workspace,
                                      approval_id=approval_id)
        if row is None:
            raise ApprovalNotFoundError(approval_id)
        return row

    # --- keputusan ---
    async def decide(self, *, workspace: str, actor: str, approval_id: int,
                     decision: str, reason: str | None = None,
                     request_id: str = "-") -> dict[str, Any]:
        row = await self.get(workspace=workspace, approval_id=approval_id)
        if row["status"] == "expired":
            raise ApprovalExpiredError()
        if row["status"] != "pending":
            raise ApprovalStateError(row["status"])
        # v1.0 S2 (SoD): peminta tak boleh menyetujui sendiri. Pemilik =
        # segmen user dari thread_id kanonis (tanpa kolom baru).
        from app.core.threads import parse_thread_id

        try:
            _, owner, _ = parse_thread_id(str(row.get("thread_id") or ""))
        except Exception:
            raise ForbiddenError(
                "Thread pemilik approval tak valid — tolak memutuskan.")
        if actor == owner:
            raise ForbiddenError(
                "Tidak boleh memutuskan approval milik sendiri "
                "(pemisahan tugas: minta reviewer lain).")
        if decision not in ("approve", "reject"):
            raise AppError("decision harus 'approve' atau 'reject'.",
                           status_code=422, code="INVALID_DECISION",
                           title="Invalid Decision")

        await self._assert_not_stale(row)

        # v1.0 R8: resume decisions bersifat positional per interrupt —
        # multi-interrupt butuh routing per-ID yang belum ada. Tolak eksplisit
        # (sebelum klaim: tanpa perubahan state).
        n_pending = await repo.count_pending_for_thread(
            self.pool, workspace=workspace, thread_id=str(row["thread_id"]))
        if n_pending > 1:
            raise ApprovalMultiInterruptError(n_pending)

        claimed = await repo.claim_decision(
            self.pool, workspace=workspace, approval_id=approval_id,
            decision=decision, actor=actor, reason=reason)
        if claimed is None:  # kalah balapan / baru kedaluwarsa
            current = await repo.get_approval(self.pool, workspace=workspace,
                                              approval_id=approval_id)
            if current and current["status"] == "expired":
                raise ApprovalExpiredError()
            raise ApprovalStateError((current or {}).get("status", "unknown"))

        # v1.0 R6: cek ulang hash SESUDAH klaim (tutup jendela TOCTOU antara
        # cek-stale dan resume). Stale di sini → lepas klaim + 409.
        try:
            await self._assert_not_stale(claimed)
        except ApprovalStaleError:
            await repo.release_claim(self.pool, workspace=workspace,
                                     approval_id=approval_id)
            await repo.append_audit(
                self.pool, workspace=workspace, actor=actor,
                action="approval_claim_released", subject_type="approval",
                subject_id=str(approval_id),
                payload={"decision": decision, "reason": "stale_setelah_klaim",
                         "thread_id": claimed["thread_id"]},
                request_id=request_id)
            raise

        await repo.append_audit(
            self.pool, workspace=workspace, actor=actor, action="approval_decided",
            subject_type="approval", subject_id=str(approval_id),
            payload={"decision": decision, "reason": reason,
                     "thread_id": claimed["thread_id"],
                     "tool": claimed["tool_name"],
                     "action_hash": claimed["action_hash"],
                     "policy_version": claimed["policy_version"]},
            request_id=request_id)
        from app.core.metrics import APPROVALS

        APPROVALS.labels(decision=decision).inc()  # v1.0 V1-F

        # v1.0 R1+R2: resume SELALU dipanggil (approve maupun reject — reject
        # tanpa resume = thread macet di interrupt selamanya). Gagal resume →
        # lepas klaim (kembali pending, boleh retry) + audit + raise asli.
        try:
            result = await self._resume(claimed, decision=decision,
                                        request_id=request_id)
        except Exception:
            await repo.release_claim(self.pool, workspace=workspace,
                                     approval_id=approval_id)
            await repo.append_audit(
                self.pool, workspace=workspace, actor=actor,
                action="approval_resume_failed", subject_type="approval",
                subject_id=str(approval_id),
                payload={"decision": decision,
                         "thread_id": claimed["thread_id"]},
                request_id=request_id)
            raise
        status = "approved" if decision == "approve" else "rejected"
        return {"approval_id": approval_id, "status": status,
                "resumed": True, "result": result}

    # --- internal ---
    async def _assert_not_stale(self, row: dict[str, Any]) -> None:
        """Hash aksi saat ini harus sama dengan yang disetujui (TOCTOU)."""
        graph = self._graph_for(str(row["agent"]), str(row["thread_id"]))
        snapshot = await graph.aget_state(
            {"configurable": {"thread_id": row["thread_id"]}})
        current = [
            {"id": getattr(i, "id", ""), "value": getattr(i, "value", None)}
            for i in getattr(snapshot, "interrupts", ()) or ()
        ]
        if not current:
            raise ApprovalStaleError()  # interrupt sudah tidak ada
        value = next((c["value"] for c in current
                      if str(c["id"]) == str(row["interrupt_id"])), None)
        if value is None:
            raise ApprovalStaleError()
        if action_hash(str(row["tool_name"]), value) != row["action_hash"]:
            raise ApprovalStaleError()

    async def _resume(self, row: dict[str, Any], *, decision: str,
                      request_id: str) -> dict[str, Any]:
        graph = self._graph_for(str(row["agent"]), str(row["thread_id"]))
        decisions = [{"type": decision} for _ in
                     (row["action_snapshot"].get("action_requests") or [{}])]
        result = await self.runner.resume(
            graph, {"decisions": decisions},
            {"configurable": {"thread_id": row["thread_id"]}})
        if result.get("status") == "pending_approval":
            await self.record_interrupts(
                workspace=row["workspace"], thread_id=str(row["thread_id"]),
                agent=str(row["agent"]),
                interrupts=result.get("interrupts") or [],
                request_id=request_id)
        return result

    def _graph_for(self, agent_name: str, thread_id: str):
        agent_cls = CHAT_AGENTS.get(agent_name)
        if agent_cls is None:
            raise AgentNotFoundError(agent_name)
        # v0.9 F0-5: WAJIB lewat factory dengan binding lengkap. Dulu context
        # dirakit di sini tanpa store/retriever sehingga graph resume punya
        # skema tool berbeda dari graph yang di-interrupt (bound_tools=[]).
        from app.agents.factory import build_agent_context

        ctx = build_agent_context(
            settings=self.settings, checkpointer=self.checkpointer,
            runner=self.runner, session_id=thread_id,
            store=self.store, pool=self.pool, rag=self.rag,
            skills=self.skills,
            agent_cls=agent_cls, missing_retriever="raise_binding",
        )
        agent: BaseChatAgent = agent_cls(ctx)
        return agent.graph


async def record_pending_approvals(app_or_request, *, identity, thread_id: str,
                                   agent: str, interrupts: list[dict[str, Any]],
                                   request_id: str = "-") -> list[dict[str, Any]]:
    """Hook dari route: catat interrupt jadi approval pending (best-effort).

    Dipanggil saat event `result` berstatus `pending_approval`. Tidak boleh
    menggagalkan stream: kegagalan dicatat sebagai log, bukan exception.
    """
    if not interrupts:
        return []
    state = getattr(getattr(app_or_request, "app", None), "state", None)
    pool = getattr(state, "pool", None)
    checkpointer = getattr(state, "checkpointer", None)
    if pool is None or checkpointer is None:
        log.warning("approval tidak dicatat: persistence belum siap")
        return []
    from app.core.config import get_settings

    svc = ApprovalService(
        get_settings(), pool, checkpointer,
        store=getattr(state, "store", None),
        rag=getattr(state, "rag", None),
        skills=getattr(state, "skills", None))
    try:
        return await svc.record_interrupts(
            workspace=identity.workspace, thread_id=thread_id, agent=agent,
            interrupts=interrupts, request_id=request_id)
    except Exception:
        log.exception("gagal mencatat approval (stream tetap jalan)")
        return []
