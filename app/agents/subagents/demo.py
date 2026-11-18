"""Subagent demo template: pengecek stok (hidup, terdaftar di roster).

Preseden: seperti ExampleChatAgent yang terdaftar di CHAT_AGENTS — fork
mendapat contoh yang BENAR-BENAR jalan, bukan cuplikan mati di docs.
Hanya aktif bila SUBAGENTS_ENABLED=true (default mati — disengaja, lihat
config: roster global vs TOOLS per-agen membuat default-on meledak LOUD).

Duplikat file ini untuk subagent baru, atau jalankan:
    python -m scripts.new_subagent <nama>
"""

from __future__ import annotations

from app.agents.subagents.registry import SubagentSpec, register

register(SubagentSpec(
    name="demo-stok",
    description="Mengecek stok barang di gudang berdasarkan item_id",
    when_to_use="user menanyakan ketersediaan atau jumlah stok barang",
    system_prompt=(
        "Kamu pengecek stok gudang. Panggil tool check_stock dengan item_id "
        "persis seperti disebut user, lalu laporkan angkanya apa adanya."
    ),
    tools=("check_stock",),
    max_tokens=2_000,
    timeout_s=20,
    thread_limit=5,
))
