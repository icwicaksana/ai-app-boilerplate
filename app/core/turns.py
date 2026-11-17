"""TurnManager: producer turn terpisah dari koneksi klien (v0.5a).

Kenapa dipisah: kalau generator SSE bergantung pada koneksi klien, disconnect
= produksi berhenti = resume mustahil. Di sini producer adalah task latar yang
menulis ke `TurnLog`; koneksi klien (SSE/WS) hanya **membaca**. Itu membuat
"putus 30 detik lalu lanjut" benar-benar bekerja, dan WS nanti tinggal menjadi
pembaca lain.

Batas yang disadari:
- Task hidup di memori proses. Restart server = producer hilang; turn tanpa
  event terminal akan tampak "orphan" saat di-resume (dilaporkan sebagai error,
  bukan menggantung).
- Tak ada pembatalan otomatis saat klien pergi: produksi tetap jalan sampai
  selesai/timeout LLM. Ini tradeoff yang disengaja (hasil tetap bisa diambil).
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.infra.turnlog import TurnLog, is_terminal

log = get_logger(__name__)


class TurnManager:
    def __init__(self, turnlog: TurnLog):
        self.turnlog = turnlog
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def enabled(self) -> bool:
        return self.turnlog.enabled

    def running(self, turn_id: str) -> bool:
        task = self._tasks.get(turn_id)
        return bool(task and not task.done())

    async def is_owner(self, *, workspace: str, turn_id: str,
                       user: str) -> bool:
        """True bila `user` pemilik turn. Owner tak ada = False (fail-closed).

        Satu-satunya pintu cek pemilik resume (dipakai HTTP + WS) agar
        semantik 410-tanpa-oracle identik di kedua transport.
        """
        owner = await self.turnlog.owner_of(workspace=workspace,
                                            turn_id=turn_id)
        return owner is not None and owner == user

    async def start(self, *, workspace: str, turn_id: str, owner: str,
                    first_event: dict[str, Any],
                    events: AsyncIterator[dict[str, Any]]) -> None:
        """Tulis event pertama (started) lalu jalankan producer di latar."""
        await self.turnlog.create(workspace=workspace, turn_id=turn_id,
                                  owner=owner, first_event=first_event)
        task = asyncio.create_task(
            self._produce(workspace, turn_id, events), name=f"turn:{turn_id}")
        self._tasks[turn_id] = task
        task.add_done_callback(lambda _t, tid=turn_id: self._tasks.pop(tid, None))

    async def _produce(self, workspace: str, turn_id: str,
                       events: AsyncIterator[dict[str, Any]]) -> None:
        terminal = False
        try:
            async for event in events:
                # `started` sudah ditulis sebagai first_event (membawa turn_id);
                # started dari runner = duplikat → lewati agar kontrak
                # "started tepat satu kali" tetap utuh.
                if event.get("type") == "started":
                    continue
                await self.turnlog.append(workspace=workspace, turn_id=turn_id,
                                          event=event)
                if is_terminal(event):
                    terminal = True
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # cache/IO error: tutup dengan error terminal
            log.exception("turn producer gagal turn=%s", turn_id)
            terminal = True
            try:
                await self.turnlog.append(workspace=workspace, turn_id=turn_id, event={
                    "type": "error", "title": "Stream Failed",
                    "status": 500, "code": "STREAM_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}", "instance": "-",
                })
            except Exception:
                log.exception("gagal menulis error terminal turn=%s", turn_id)
        finally:
            if terminal:
                await self.turnlog.finish(workspace=workspace, turn_id=turn_id)

    async def follow(self, *, workspace: str, turn_id: str, cursor: str = "0-0",
                     idle_timeout_s: float = 60.0) -> AsyncIterator[tuple[str, dict]]:
        async for item in self.turnlog.follow(
                workspace=workspace, turn_id=turn_id, cursor=cursor,
                idle_timeout_s=idle_timeout_s):
            yield item

    async def cancel_all(self) -> None:
        """Shutdown: hentikan producer yang masih jalan (tanpa menulis terminal)."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            log.info("turn producer dibatalkan saat shutdown: %d", len(tasks))
        self._tasks.clear()
