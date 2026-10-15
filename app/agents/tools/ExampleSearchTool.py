"""Contoh tool v0. Duplikat file ini untuk tool baru (satu tool = satu file + satu nama ClassName)."""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def check_stock(item_id: str) -> str:
    """Cek stok barang di gudang berdasarkan item_id. Contoh: 'ITEM-99'."""
    # TODO: ganti dengan query DB/API sungguhan di project baru.
    # Sengaja deterministik agar v0 bisa di-test tanpa DB eksternal.
    return f"Stok untuk barang {item_id}: 42 unit tersedia."
