"""ThreadController: inspeksi + time-travel + hapus thread (rel state, v0.3).

- Bekerja di LEVEL CHECKPOINTER (`aget_tuple`/`alist`/`adelete_thread`) — tanpa
  butuh graph agen. Hanya `fork` butuh graph (untuk `aupdate_state`) sehingga
  request fork wajib sertakan `agent`.
- Setiap method: parse thread (400) → cocok pemilik (403) → cek ada (404).
  Urutan ini penting: jangan bocorkan keberadaan thread workspace lain (403 dulu).
- DELETE hanya hapus checkpoints. Profil/fakta user di store TIDAK tersentuh
  (hapus chat ≠ hapus ingatan).
"""

from __future__ import annotations

import copy
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.AgentContext import AgentContext
from app.agents.utils.MessageUtils import coerce_llm_text
from app.controllers.base import BaseController
from app.controllers.registry import CHAT_AGENTS, JOB_AGENTS
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import (
    AgentNotFoundError,
    InvalidThreadIdError,
    ThreadNotFoundError,
    UpstreamAIError,
)
from app.core.threads import ThreadIdentity, compose_thread_id, ensure_owner
from app.infra.memory import PROFILE_MSG_ID
from app.schemas.threads import (
    ForkResponse,
    HistoryItem,
    HistoryResponse,
    ThreadMessagePreview,
    ThreadStateResponse,
)

MAX_PREVIEW_CHARS = 2000


def _role_of(msg) -> tuple[str, str | None]:
    """(role, tool_name|None). Defensif: dict mentah ikut didukung."""
    if isinstance(msg, HumanMessage):
        return "user", None
    if isinstance(msg, AIMessage):
        return "assistant", None
    if isinstance(msg, SystemMessage):
        return "system", None
    if isinstance(msg, ToolMessage):
        return "tool", getattr(msg, "name", None)
    if isinstance(msg, dict):
        # Serialisasi LangChain pakai "human"/"ai" — petakan ke kosakata kontrak.
        role = str(msg.get("role", msg.get("type", "unknown"))).lower()
        role = {"human": "user", "ai": "assistant"}.get(role, role)
        if role not in ("user", "assistant", "system", "tool"):
            role = "unknown"
        return role, msg.get("name")
    return "unknown", None


def _is_profile_msg(msg) -> bool:
    """Pesan infrastruktur profil (wajib disembunyikan dari preview)."""
    if isinstance(msg, dict):
        return msg.get("id") == PROFILE_MSG_ID
    return getattr(msg, "id", None) == PROFILE_MSG_ID


def preview_message(msg) -> ThreadMessagePreview:
    """1 pesan → preview (cap 2000 char). Murni, mudah di-test."""
    role, tool = _role_of(msg)
    content = getattr(msg, "content", msg) if not isinstance(msg, dict) else msg.get("content", "")
    text = coerce_llm_text(content)
    truncated = len(text) > MAX_PREVIEW_CHARS
    return ThreadMessagePreview(
        role=role, content=text[:MAX_PREVIEW_CHARS],
        truncated=truncated, tool=tool,
    )


def _conversational(messages: list) -> list:
    """Buang pesan infrastruktur (profil) — preview hanya isi percakapan."""
    return [m for m in messages if not _is_profile_msg(m)]


def _count_conversational(messages) -> int:
    if not isinstance(messages, list):
        return 0
    return len(_conversational(messages))


def _checkpoint_id_of(tup) -> str:
    return str((tup.config.get("configurable") or {}).get("checkpoint_id", ""))


def _next_nodes_of(tup) -> list[str] | None:
    """Node berikutnya dari checkpoint tuple (v0.6, untuk dashboard approval)."""
    meta = getattr(tup, "metadata", None) or {}
    nxt = meta.get("next") if isinstance(meta, dict) else None
    if nxt is None:
        nxt = getattr(tup, "next", None)
    if not nxt:
        return None
    return [str(n) for n in nxt] if isinstance(nxt, (list, tuple)) else [str(nxt)]


def _interrupt_ids_of(tup) -> list[str] | None:
    """ID interrupt yang menunggu (dari checkpoint tuple, bila tersedia)."""
    items = getattr(tup, "interrupts", None) or ()
    ids = [str(getattr(i, "id", "")) for i in items]
    ids = [i for i in ids if i]
    return ids or None


def _values_of(tup) -> dict:
    cp = getattr(tup, "checkpoint", None) or {}
    if isinstance(cp, dict):
        values = cp.get("channel_values", {})
        return values if isinstance(values, dict) else {}
    return {}


def _meta_of(tup, key: str):
    meta = getattr(tup, "metadata", None) or {}
    return meta.get(key) if isinstance(meta, dict) else None


class ThreadController(BaseController):
    def __init__(self, settings: Settings, checkpointer, runner: Runner | None = None):
        super().__init__(settings, checkpointer, runner or Runner())

    async def _require_tuple(self, thread_id: str, checkpoint_id: str | None = None):
        """Tuple terbaru (atau 1 checkpoint spesifik). None/absen → 404.

        `aget_tuple`/`alist` TIDAK raise untuk thread asing (hasil spike S-3) —
        sehingga cek eksplisit ini adalah satu-satunya sumber 404.
        """
        configurable: dict = {"thread_id": thread_id}
        if checkpoint_id:
            configurable["checkpoint_id"] = checkpoint_id
        tup = await self.checkpointer.aget_tuple({"configurable": configurable})
        if tup is None:
            raise ThreadNotFoundError(
                thread_id if not checkpoint_id else f"{thread_id}@{checkpoint_id}"
            )
        return tup

    async def get_state(self, identity: ThreadIdentity, thread_id: str,
                        *, last_n: int | None = None,
                        request_id: str = "-") -> ThreadStateResponse:
        started = time.perf_counter()
        ensure_owner(identity, thread_id)
        tup = await self._require_tuple(thread_id.strip().lower())
        messages = _values_of(tup).get("messages", []) or []
        if not isinstance(messages, list):
            messages = []
        convo = _conversational(messages)
        if last_n is not None:
            convo = convo[max(0, len(convo) - max(1, last_n)):]
        previews = [preview_message(m) for m in convo]
        self.log_done("thread state", request_id=request_id, thread_id=thread_id,
                      agent="-", status="completed", started=started)
        return ThreadStateResponse(
            thread_id=thread_id.strip().lower(),
            checkpoint_id=_checkpoint_id_of(tup),
            step=_meta_of(tup, "step"),
            source=_meta_of(tup, "source"),
            created_at=(tup.checkpoint.get("ts") if isinstance(
                getattr(tup, "checkpoint", None), dict) else None),
            messages_count=len(previews),
            messages=previews,
            next=_next_nodes_of(tup),
            interrupt_ids=_interrupt_ids_of(tup),
        )

    async def get_history(self, identity: ThreadIdentity, thread_id: str,
                          *, limit: int = 20, offset: int = 0,
                          request_id: str = "-") -> HistoryResponse:
        # Catatan skala (keputusan sadar v0.3): full-scan O(N) + sort ts di Python.
        # Benar untuk thread SMB (ratusan checkpoint, ms). Paginasi keyset di DB
        # (alist+before) ditunda ke v0.4+ bila ada thread raksasa sungguhan.
        started = time.perf_counter()
        ensure_owner(identity, thread_id)
        tid = thread_id.strip().lower()
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        tuples = [t async for t in self.checkpointer.alist(
            {"configurable": {"thread_id": tid}})]
        if not tuples:
            raise ThreadNotFoundError(tid)
        # Terbaru dulu (eksplisit sort by ts — jangan andalkan urutan alist).
        def _ts(t) -> str:
            cp = getattr(t, "checkpoint", None)
            return cp.get("ts", "") if isinstance(cp, dict) else ""
        tuples.sort(key=_ts, reverse=True)
        items = []
        for t in tuples:
            msgs = _values_of(t).get("messages", []) or []
            cp = getattr(t, "checkpoint", None)
            items.append(HistoryItem(
                checkpoint_id=_checkpoint_id_of(t),
                step=_meta_of(t, "step"),
                source=_meta_of(t, "source"),
                created_at=cp.get("ts") if isinstance(cp, dict) else None,
                messages_count=_count_conversational(msgs),
            ))
        self.log_done("thread history", request_id=request_id, thread_id=tid,
                      agent="-", status="completed", started=started)
        return HistoryResponse(
            thread_id=tid, items=items[offset:offset + limit],
            limit=limit, offset=offset, total=len(items),
        )

    async def fork(self, identity: ThreadIdentity, thread_id: str,
                   checkpoint_id: str, agent_name: str,
                   *, request_id: str = "-") -> ForkResponse:
        # Fork = operasi DB murni (salin values) — TAK PERNAH invoke LLM.
        # `agent` dipercaya dari pemanggil; mismatch rel (chat vs pipeline) akan
        # gagal jelas di aupdate_state (skema state). Validasi silang-rel via
        # metadata pemilik ditunda (butuh tulis metadata di semua jalur).
        started = time.perf_counter()
        workspace, user, _ = ensure_owner(identity, thread_id)
        agents = {**CHAT_AGENTS, **JOB_AGENTS}
        if agent_name not in agents:  # lookup DULU (sebelum I/O sia-sia)
            raise AgentNotFoundError(agent_name)
        tid = thread_id.strip().lower()
        cid = (checkpoint_id or "").strip()
        if not cid:  # "" falsy → _require_tuple akan ambil latest (SALAH)
            raise InvalidThreadIdError(f"{tid}@<checkpoint kosong>")
        tup = await self._require_tuple(tid, cid)
        new_thread = compose_thread_id(workspace, user)
        graph = self._graph_for_fork(agents[agent_name], new_thread)
        await graph.aupdate_state(
            {"configurable": {"thread_id": new_thread}},
            copy.deepcopy(_values_of(tup)),  # dalam: jangan bagi ref messages
        )
        self.log_done("thread fork", request_id=request_id, thread_id=new_thread,
                      agent=agent_name, status="completed", started=started)
        return ForkResponse(thread_id=new_thread, forked_from=cid,
                            agent=agent_name)

    def _graph_for_fork(self, agent_cls, new_thread: str):
        """Graph untuk tulis state awal fork. Tahan kredensial LLM kosong.

        Build normal butuh LITELLM_* (get_llm raise 502 bila kosong) padahal
        fork tak menyentuh model — fallback ke kredensial placeholder agar fork
        tetap jalan sebagai operasi DB murni.
        """
        try:
            ctx = AgentContext(
                settings=self.settings, checkpointer=self.checkpointer,
                runner=self.runner, session_id=new_thread,
            )
            return agent_cls(ctx).graph
        except (UpstreamAIError, ValueError):
            placeholder = self.settings.model_copy(update={
                "LITELLM_API_KEY": "fork-tanpa-llm", "LITELLM_MODEL": "fork-tanpa-llm",
            })
            ctx = AgentContext(
                settings=placeholder, checkpointer=self.checkpointer,
                runner=self.runner, session_id=new_thread,
            )
            return agent_cls(ctx).graph

    async def delete_thread(self, identity: ThreadIdentity, thread_id: str,
                            *, request_id: str = "-") -> None:
        started = time.perf_counter()
        ensure_owner(identity, thread_id)
        tid = thread_id.strip().lower()
        await self._require_tuple(tid)  # 404 dulu (`adelete_thread` no-op bila asing)
        await self.checkpointer.adelete_thread(tid)
        self.log_done("thread delete", request_id=request_id, thread_id=tid,
                      agent="-", status="completed", started=started)
