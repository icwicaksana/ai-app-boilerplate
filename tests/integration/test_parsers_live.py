"""Live parser: Docling (model) + Tesseract (binary). Skip bila prasyarat mati."""

import io
import shutil

import pytest

pytestmark = pytest.mark.integration


def _pdf_2halaman() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Kebijakan Retur Toko Maju Jaya. " * 4)
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Syarat: bawa nota pembelian asli. " * 4)
    return doc.tobytes()


@pytest.mark.asyncio
async def test_live_docling_pdf_2_halaman():
    import os

    os.environ.setdefault(
        "HF_HUB_CACHE",
        str(__import__("pathlib").Path(".hf-cache").resolve()))
    from app.infra.rag.docling_parser import DoclingParser

    out = await DoclingParser().parse(_pdf_2halaman(),
                                      mime="application/pdf")
    assert len(out["pages"]) == 2
    combined = " ".join(p["text"] for p in out["pages"])
    # Docling bisa menggabung paragraf lintas-halaman (prov [1,2]) → isi utuh
    # di halaman pertama + penanda span; atribusi presisi diuji via stub (unit).
    assert "Retur" in combined and "nota" in combined
    assert out["is_scanned"] is False


@pytest.mark.asyncio
async def test_live_tesseract_pdf_gambar():
    if shutil.which("tesseract") is None:
        pytest.skip("binary tesseract tak ada (install manual, D10)")
    import pymupdf

    from app.infra.rag.tesseract import TesseractEngine

    src = pymupdf.open()
    pg = src.new_page()
    pg.insert_text((72, 72), "Denda dua persen per bulan", fontsize=24)
    pix = pg.get_pixmap(dpi=300)
    img_pdf = pymupdf.open()
    img_pdf.new_page(width=pix.width, height=pix.height).insert_image(
        pymupdf.Rect(0, 0, pix.width, pix.height), pixmap=pix)
    blob = img_pdf.tobytes()

    out = await TesseractEngine().ocr(
        blob, langs="ind+eng", timeout_seconds=120, max_pages=50)
    norm = out["text"].lower().replace(" ", "")
    assert "denda" in norm and "persen" in norm, out["text"][:200]
    assert 0.0 <= out["confidence"] <= 1.0
