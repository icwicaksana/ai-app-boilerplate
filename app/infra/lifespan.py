"""Rutin entrypoint bersama: API (`app.main`) + worker (`app.workers.*`).

D1/P1-11: JANGAN `from app.main import ...` dari worker — modul `app.main`
menginstansiasi FastAPI saat import (`app = create_app()`). Semua entrypoint
impor dari sini (ringan, tanpa FastAPI).
"""

from __future__ import annotations

import asyncio
import sys


def ensure_selector_loop() -> None:
    """Paksa SelectorEventLoop (Windows): psycopg async MENOLAK Proactor.

    Dipanggil eksplisit dari entrypoint — BUKAN saat import (import-time side
    effect dihapus, temuan core #1). Nilai loop programmatic
    `asyncio:SelectorEventLoop` VALID (uvicorn resolve ke kelas loop); yang
    INVALID adalah flag CLI `--loop` (choices terbatas) — jangan resepkan
    CLI flag itu di mana pun. Lifespan tetap berjaga (Proactor terdeteksi →
    gagal cepat dengan pesan aksi).
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
