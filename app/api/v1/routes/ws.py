"""WebSocket realtime (v0.5b): envelope sama dengan SSE + seq.

Alur:
1. Klien `POST /realtime/ticket` (Bearer) → ticket sekali pakai (TTL 30 dtk).
2. Klien `WS /ws/chat?ticket=...` (Origin divalidasi — CSWSH).
3. Klien kirim `{"type":"chat","agent":...,"input":...,"thread_id":...}`.
4. Server ack `{"type":"ack","turn_id":...}` lalu memompa event envelope
   (`started/token/thinking/tool_*/result|error`) dari turn log dengan
   field tambahan `seq` (monotonic per koneksi). Disconnect = pump berhenti,
   produsen jalan terus (v1.0 R4: paritas resume HTTP).
5. `{"type":"cancel"}` menghentikan run aktif; `{"type":"ping"}` → `pong`.
6. `{"type":"resume","turn_id":...,"last_event_id":...}` melanjutkan turn dari
   turn log (v0.5a) — jalur yang sama dengan SSE resume.

Backpressure: queue per koneksi dibatasi `WS_MAX_QUEUE`. Saat penuh, frame
`token`/`thinking` DI-DROP (aman: klien bisa resume via turn log), frame
terminal TIDAK pernah di-drop. Klien yang terus lambat diputus dengan 1013.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from app.controllers.ChatController import ChatController
from app.core.auth import is_revoked, require_scope
from app.core.config import Settings, get_settings
from app.core.dependencies import get_chat_controller, get_thread_identity
from app.core.errors import AppError, RateLimitedError, StorageUnavailableError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimiter
from app.core.threads import ThreadIdentity
from app.core.turns import TurnManager
from app.core.ws_ticket import TicketStore, origin_allowed
from app.infra.cache import CacheClient, CacheUnavailable
from app.infra.turnlog import is_terminal, new_turn_id
from app.schemas.chat import ChatRequest
from app.schemas.stream import StreamStarted

router = APIRouter(tags=["realtime"])
log = get_logger(__name__)

SUBPROTOCOL = "ai-app.v1"
CLOSE_POLICY = 1008
CLOSE_TRY_AGAIN = 1013
CLOSE_RESTART = 1012
CLOSE_AUTH_EXPIRED = 4001  # kode aplikasi: sesi WS habis, reconnect + resume

DROPPABLE = frozenset({"token", "thinking"})


@router.post("/realtime/ticket")
async def create_ticket(
    request: Request,
    identity: ThreadIdentity = Depends(get_thread_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Terbitkan ticket sekali pakai untuk handshake WS."""
    from app.core.dependencies import get_current_claims

    cache = getattr(request.app.state, "cache", None)
    # v1.0 S6: ticket = gerbang WS — batasi per user + wajib scope chat.
    # (Router-level dep tak dipakai: ws_chat handshake tanpa Bearer.)
    claims = await get_current_claims(request, settings, cache)
    require_scope(claims, "chat")
    decision = await RateLimiter(cache).check(
        scope="ws_ticket", workspace=identity.workspace,
        subject=identity.user, limit=settings.WS_TICKET_PER_MIN)
    if not decision.allowed:
        raise RateLimitedError(decision.retry_after, decision.limit)
    store = TicketStore(cache, settings.WS_TICKET_TTL_SECONDS)
    try:
        ticket, ttl = await store.issue(workspace=identity.workspace,
                                        user=identity.user,
                                        scopes=claims.scopes, jti=claims.jti)
    except CacheUnavailable as exc:
        # Tiket butuh cache (sekali-pakai tak bisa tanpa simpan) → 503 jujur,
        # bukan 500 generik (S9: bedakan endpoint sensitif).
        raise StorageUnavailableError(
            "Layanan ticket WebSocket butuh cache — coba lagi nanti.") from exc
    return {"ticket": ticket, "expires_in": ttl,
            "ws_url": f"{settings.API_V1_PREFIX}/ws/chat?ticket={ticket}",
            "subprotocol": SUBPROTOCOL}


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    settings = get_settings()
    if not settings.WS_ENABLED:
        await websocket.close(code=CLOSE_TRY_AGAIN)
        return
    if not origin_allowed(websocket.headers.get("origin"), settings.ws_origins()):
        log.warning("WS ditolak: Origin tidak diizinkan (%s)",
                    websocket.headers.get("origin"))
        await websocket.close(code=CLOSE_POLICY)
        return

    cache = getattr(websocket.app.state, "cache", None)
    store = TicketStore(cache, settings.WS_TICKET_TTL_SECONDS)
    identity = await store.consume(websocket.query_params.get("ticket", ""))
    if identity is None:
        await websocket.close(code=CLOSE_POLICY)
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    conn = _Connection(websocket, identity, settings, cache)
    await conn.run()


# --- internal ---

def _controller_for(ws: WebSocket, settings: Settings) -> ChatController:
    """Bangun controller IDENTIK dengan jalur HTTP (v1.0 S7).

    Cermin get_chat_controller: guard checkpointer/store (503 eksplisit,
    bukan jalan-diam-tanpa-persistensi) + wiring pool/rag/skills yang sama
    (rag_chat via WS sebelumnya selalu kehilangan retriever).
    """
    from app.core.dependencies import (
        _optional_pool,
        _persistence,
        get_chat_runner,
        get_rag_clients,
    )

    checkpointer, store = _persistence(ws)  # type: ignore[arg-type]
    return ChatController(settings, checkpointer, get_chat_runner(),
                          store=store, pool=_optional_pool(ws),  # type: ignore[arg-type]
                          rag=get_rag_clients(ws),  # type: ignore[arg-type]
                          skills=getattr(ws.app.state, "skills", None))


class _Connection:
    def __init__(self, ws: WebSocket, identity, settings: Settings,
                 cache: CacheClient | None):
        self.ws = ws
        self.identity = identity
        self.settings = settings
        self.cache = getattr(ws.app.state, "cache", cache)
        self.turns: TurnManager = getattr(ws.app.state, "turns", TurnManager.__new__(TurnManager))
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue(
            maxsize=settings.WS_MAX_QUEUE)
        self.seq = 0
        self.dropped = 0
        self.run_task: asyncio.Task | None = None
        self._sender: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None

    # --- lifecycle ---
    async def run(self) -> None:
        self._sender = asyncio.create_task(self._send_loop())
        self._watchdog = asyncio.create_task(self._lifetime_watchdog())
        # `ready` memberi tahu klien umur koneksi + heartbeat, sehingga klien
        # bisa menjadwalkan re-ticket + reconnect + resume SEBELUM diputus.
        await self._send({"type": "ready", "subprotocol": SUBPROTOCOL,
                          "expires_in": self.settings.WS_MAX_CONNECTION_SECONDS,
                          "heartbeat": self.settings.WS_HEARTBEAT_SECONDS})
        try:
            while True:
                msg = await self.ws.receive_json()
                await self._handle(msg)
        except WebSocketDisconnect:
            log.info("WS disconnect workspace=%s user=%s", *self._id())
        except Exception:
            log.exception("WS loop gagal workspace=%s user=%s", *self._id())
        finally:
            await self._cleanup()

    async def _lifetime_watchdog(self) -> None:
        """Putuskan koneksi setelah batas umur (identitas hanya dicek saat handshake)."""
        await asyncio.sleep(max(1, self.settings.WS_MAX_CONNECTION_SECONDS))
        try:
            await self._send({"type": "auth_expired",
                              "reason": "max_connection_lifetime",
                              "close_code": CLOSE_AUTH_EXPIRED})
        except Exception:
            log.debug("gagal kirim auth_expired (koneksi sudah tutup)",
                      exc_info=True)
        await self.ws.close(code=CLOSE_AUTH_EXPIRED)

    def _id(self) -> tuple[str, str]:
        return self.identity.workspace, self.identity.user

    async def _cleanup(self) -> None:
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            with contextlib.suppress(BaseException):
                await self.run_task
        for task in (self._sender, getattr(self, "_watchdog", None)):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        await self._presence(offline=True)

    # --- handlers ---
    async def _handle(self, msg: dict) -> None:
        kind = str(msg.get("type", ""))
        if kind == "ping":
            await self._put({"type": "pong"})
        elif kind == "cancel":
            if self.run_task and not self.run_task.done():
                self.run_task.cancel()
                await self._put({"type": "cancelled"})
        elif kind in ("chat", "resume"):
            # v1.0 S6: gate scope + rate-limit per pesan (paritas HTTP).
            err = await self._gate(kind)
            if err is not None:
                await self._put(err)
            elif kind == "chat":
                await self._start_chat(msg)
            else:
                await self._start_resume(msg)
        else:
            await self._put({"type": "error", "code": "BAD_MESSAGE",
                             "status": 400, "title": "Bad Message",
                             "detail": f"Tipe pesan tak dikenal: {kind!r}",
                             "instance": "-"})

    async def _gate(self, kind: str) -> dict | None:
        """Frame error bila ditolak; None bila lolos. (v1.0 S6, teruji)."""
        if "chat" not in (getattr(self.identity, "scopes", None) or ()):
            return {"type": "error", "code": "FORBIDDEN", "status": 403,
                    "title": "Forbidden",
                    "detail": "Ticket tidak punya scope 'chat'.",
                    "instance": "-"}
        if not self.settings.RATE_LIMIT_ENABLED:
            return None
        limit = (self.settings.WS_CHAT_PER_MIN if kind == "chat"
                 else self.settings.WS_RESUME_PER_MIN)
        decision = await RateLimiter(self.cache).check(
            scope=f"ws_{kind}", workspace=self.identity.workspace,
            subject=self.identity.user, limit=limit)
        if decision.degraded:
            from app.core.degraded import report_degraded

            report_degraded("rate_limit", f"ws_{kind}")
        if not decision.allowed:
            log.warning("WS rate-limit ditolak kind=%s user=%s", kind,
                        self.identity.user)
            return {"type": "error", "code": "RATE_LIMITED", "status": 429,
                    "title": "Too Many Requests",
                    "detail": f"Terlalu banyak pesan {kind} "
                              f"(limit {limit}/menit).",
                    "retry_after": decision.retry_after, "instance": "-"}
        return None

    async def _auth_still_valid(self) -> bool:
        """Cek revoke berkala (v1.0 S6): logout memutus WS ≤1 heartbeat.

        Tanpa jti (ticket pra-v1.0, TTL 30 dtk) → anggap valid; koneksi
        lama mati sendiri via watchdog umur maksimum.
        """
        jti = getattr(self.identity, "jti", None)
        if not jti:
            return True
        try:
            return not await is_revoked(self.cache, jti)
        except Exception:
            from app.core.degraded import report_degraded

            report_degraded("ws_auth", "cek_gagal")
            return True  # fail-open seperti HTTP (kini beralarm, S9)
    async def _start_chat(self, msg: dict) -> None:
        if self.run_task and not self.run_task.done():
            await self._put({"type": "error", "code": "BUSY", "status": 409,
                             "title": "Busy",
                             "detail": "Masih ada run aktif; kirim cancel dulu.",
                             "instance": "-"})
            return
        try:
            req = ChatRequest(agent=msg.get("agent", "example_chat"),
                              input=msg.get("input", ""),
                              thread_id=msg.get("thread_id"))
        except Exception as exc:
            await self._put({"type": "error", "code": "VALIDATION_ERROR",
                             "status": 422, "title": "Validation Error",
                             "detail": str(exc), "instance": "-"})
            return
        ctrl = None
        try:
            ctrl = _controller_for(self.ws, self.settings)
        except StorageUnavailableError as exc:
            await self._put({"type": "error", "code": exc.code,
                             "status": exc.status_code, "title": exc.title,
                             "detail": exc.message, "instance": "-"})
            return
        # R13 (sisi WS): validasi penuh SEBELUM turn dibuat — tanpa ini turn
        # yatim + error datang sebagai event, bukan penolakan bersih.
        try:
            thread_id = ctrl.validate_before_stream(req, self.identity)
        except AppError as exc:
            await self._put({"type": "error", "code": exc.code,
                             "status": exc.status_code, "title": exc.title,
                             "detail": exc.message, "instance": "-"})
            return
        if not getattr(self.turns, "enabled", False):
            # Tanpa cache: direct seperti HTTP (tak bisa resume).
            self.run_task = asyncio.create_task(
                self._run_chat_direct(ctrl, req))
            return
        # v1.0 R4: WS chat lewat TurnManager — disconnect = pump berhenti,
        # produsen jalan terus, resume via turn_id (paritas HTTP).
        turn_id = new_turn_id()
        try:
            await self.turns.start(
                workspace=self.identity.workspace, turn_id=turn_id,
                owner=self.identity.user,
                first_event=StreamStarted(turn_id=turn_id).model_dump(
                    mode="json"),
                events=ctrl.stream(req, self.identity, request_id="ws"),
                on_event=self._approval_hook(thread_id, req.agent))
        except CacheUnavailable:
            await self._put({"type": "error", "code": "STORAGE_UNAVAILABLE",
                             "status": 503, "title": "Storage Unavailable",
                             "detail": "Turn log tak tersedia — coba lagi.",
                             "instance": "-"})
            return
        # Ack KILAT: klien pegang turn_id bahkan bila putus seketika.
        await self._put({"type": "ack", "turn_id": turn_id})
        self.run_task = asyncio.create_task(self._pump_turn(turn_id, "0-0"))

    async def _pump_turn(self, turn_id: str, cursor: str) -> None:
        """Pompa turn log ke WS (dipakai chat-managed + resume)."""
        async for _rid, event in self.turns.follow(
                workspace=self.identity.workspace, turn_id=turn_id,
                cursor=cursor):
            await self._put(event)

    def _approval_hook(self, thread_id: str, agent: str):
        """Hook produsen (R3): interrupt → approval pending."""

        async def _hook(event: dict) -> None:
            await self._record(event, thread_id, agent)

        return _hook

    async def _run_chat_direct(self, ctrl: ChatController, req: ChatRequest) -> None:
        """Jalur tanpa cache: pompa langsung (konsumen = produsen).

        Di sini _record per event BENAR (R3 tak berlaku: tak ada TurnLog).
        Disconnect = run dibatalkan (tak bisa resume — sama seperti HTTP).
        """
        thread_id = ""
        try:
            thread_id = ctrl.validate_before_stream(req, self.identity)
            async for event in ctrl.stream(req, self.identity, request_id="ws"):
                await self._put(event)
                await self._record(event, thread_id, req.agent)
        except AppError as exc:
            await self._put({"type": "error", "code": exc.code,
                             "status": exc.status_code, "title": exc.title,
                             "detail": exc.message, "instance": "-"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("WS run gagal")
            from app.helpers.response import stream_error_from

            await self._put(stream_error_from(exc))

    async def _record(self, event: dict, thread_id: str, agent: str) -> None:
        """v0.6: interrupt → approval pending (sama seperti jalur SSE)."""
        if event.get("type") != "result" or event.get("status") != "pending_approval":
            return
        from app.core.approvals import record_pending_approvals

        await record_pending_approvals(
            self.ws, identity=self.identity, thread_id=thread_id,
            agent=agent, interrupts=event.get("interrupts") or [],
            request_id="ws")

    async def _start_resume(self, msg: dict) -> None:
        turn_id = str(msg.get("turn_id") or "")
        cursor = str(msg.get("last_event_id") or "0-0")
        # v1.0 R11: resume tak boleh menimpa run aktif (dulu pump lama bocor
        # + dua pompa berebut queue). Samakan dengan chat: 409 BUSY.
        if self.run_task and not self.run_task.done():
            await self._put({"type": "error", "code": "BUSY", "status": 409,
                             "title": "Busy",
                             "detail": "Masih ada run aktif; kirim cancel dulu.",
                             "instance": "-"})
            return
        if not turn_id or not getattr(self.turns, "enabled", False):
            await self._put({"type": "error", "code": "STREAM_GONE",
                             "status": 410, "title": "Gone",
                             "detail": "Turn tidak tersedia.", "instance": "-"})
            return
        if not await self.turns.turnlog.exists(workspace=self.identity.workspace,
                                               turn_id=turn_id):
            await self._put({"type": "error", "code": "STREAM_GONE",
                             "status": 410, "title": "Gone",
                             "detail": f"Turn '{turn_id}' kedaluwarsa.",
                             "instance": "-"})
            return
        # v1.0 S3: sama seperti HTTP — salah-pemilik = 410 tanpa oracle.
        if not await self.turns.is_owner(workspace=self.identity.workspace,
                                         turn_id=turn_id,
                                         user=self.identity.user):
            await self._put({"type": "error", "code": "STREAM_GONE",
                             "status": 410, "title": "Gone",
                             "detail": f"Turn '{turn_id}' kedaluwarsa.",
                             "instance": "-"})
            return

        self.run_task = asyncio.create_task(self._pump_turn(turn_id, cursor))

    # --- queue & pengiriman ---
    async def _put(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            if str(event.get("type")) in DROPPABLE:
                self.dropped += 1
                if self.dropped % 100 == 1:
                    log.warning("WS backpressure: drop %d frame (user=%s)",
                                self.dropped, self.identity.user)
                return
            # Frame penting (started/result/error/tool) → putus klien lambat.
            await self.ws.close(code=CLOSE_TRY_AGAIN)
            raise WebSocketDisconnect(code=CLOSE_TRY_AGAIN)

    async def _send_loop(self) -> None:
        interval = float(self.settings.WS_HEARTBEAT_SECONDS)
        while True:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                # v1.0 S6: cek revoke tiap heartbeat — logout memutus WS.
                if not await self._auth_still_valid():
                    log.info("WS ditutup: token di-revoke (logout) user=%s",
                             self.identity.user)
                    await self._send({"type": "auth_expired",
                                      "reason": "token_revoked",
                                      "close_code": CLOSE_AUTH_EXPIRED})
                    await self.ws.close(code=CLOSE_AUTH_EXPIRED)
                    return
                await self._presence()  # refresh TTL presence
                await self._send({"type": "heartbeat", "ts": int(time.time())})
                continue
            if item is None:
                return
            await self._send(item)

    async def _send(self, event: dict) -> None:
        self.seq += 1
        payload = {**event, "seq": self.seq}
        await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
        if is_terminal(event):
            await self._presence()

    async def _presence(self, offline: bool = False) -> None:
        if self.cache is None:
            return
        key = f"ws:presence:{self.identity.workspace}:{self.identity.user}"
        try:
            if offline:
                await self.cache.delete(key)
            else:
                await self.cache.set_json(
                    key, {"user": self.identity.user},
                    ttl_seconds=max(2 * self.settings.WS_HEARTBEAT_SECONDS, 10))
        except Exception:
            log.debug("presence update gagal (diabaikan)", exc_info=True)
