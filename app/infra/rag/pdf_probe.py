"""Probe PDF murah (pypdf): page-count + panjang teks — TANPA model layout.

Dipakai worker SEBELUM parse berat (Docling/OCR) untuk:
- tolak `MAX_PAGES_PDF` (413) dengan cepat,
- putuskan `OCR_REQUIRED` vs parse langsung (D10: <50 char/halaman).
"""

from __future__ import annotations

import asyncio
import io


async def probe_pdf(blob: bytes) -> dict:
    return await asyncio.to_thread(_probe_sync, blob)


def _probe_sync(blob: bytes) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    from app.core.errors import UnsupportedMediaError

    try:
        reader = PdfReader(io.BytesIO(blob))
        pages = len(reader.pages)
    except (PdfReadError, ValueError) as exc:
        # PDF rusak/terenkripsi/wrong-magic → terminal JELAS, bukan retry
        # 5x sia-sia (P13). Password-protected = unsupported (tanpa fitur
        # buka-kunci di v0.7).
        raise UnsupportedMediaError(f"PDF tak terbaca: {exc}") from exc
    chars = 0
    for page in reader.pages:
        try:
            chars += len(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - halaman rusak = 0 char
            continue
    per_page = (chars / pages) if pages else 0
    return {"pages": pages, "chars": chars,
            "chars_per_page": per_page, "looks_scanned": per_page < 50}
