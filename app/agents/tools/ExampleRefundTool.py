"""Tool demo refund (v0.6) — target HITL threshold.

SENGAJA deterministik & tanpa efek nyata: template tidak boleh memindahkan
uang. Ganti isi fungsi dengan panggilan API pembayaran saat dipakai klien,
dan pertahankan pola approval-nya.
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def process_refund(order_id: str, amount: float, reason: str = "") -> str:
    """Proses refund untuk sebuah order (nominal dalam Rupiah).

    Refund di atas Rp 500.000 akan dijeda untuk persetujuan manusia sebelum
    dieksekusi. Contoh: order_id='ORD-1', amount=750000.
    """
    # TODO(klien): ganti dengan panggilan payment gateway (idempotency key =
    # order_id + amount). Saat ini stub deterministik untuk demo/test.
    return (f"Refund {amount:.0f} untuk {order_id} diproses "
            f"(alasan: {reason or '-'}).")
