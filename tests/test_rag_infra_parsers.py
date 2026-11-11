"""Unit parser infra: mapping docling (stub) + xlsx + probe PDF (pymupdf).

Tanpa download model / binary eksternal (itu ranah integration)."""

import io
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app.core.errors import UnsupportedMediaError
from app.infra.rag.docling_parser import docling_to_parsed
from app.infra.rag.pdf_probe import probe_pdf
from app.infra.rag.xlsx import XlsxParser


def _item(text, page=1, label="PARAGRAPH"):
    return SimpleNamespace(text=text, label=label,
                           prov=[SimpleNamespace(page_no=page)])


def _table(grid, page=2):
    rows = [[SimpleNamespace(text=c) for c in r] for r in grid]
    return SimpleNamespace(data=SimpleNamespace(grid=rows),
                           prov=[SimpleNamespace(page_no=page)])


def _doc(texts, tables, num_pages=2):
    return SimpleNamespace(texts=texts, tables=tables,
                           num_pages=lambda: num_pages)


def test_mapping_halaman_dan_heading():
    doc = _doc([_item("Judul Besar", 1, "TITLE"), _item("Isi satu.", 1),
                _item("Isi dua.", 2)], [], num_pages=2)
    out = docling_to_parsed(doc, "application/pdf")
    assert [p["page"] for p in out["pages"]] == [1, 2]
    assert out["pages"][0]["headings"] == ["Judul Besar"]
    assert "Isi dua." in out["pages"][1]["text"]
    assert out["is_scanned"] is True  # teks minim → kandidat OCR
    assert out["parser"] == "docling"


def test_mapping_tabel_dan_bukan_pindai():
    texts = [_item("x " * 60, 1), _item("y " * 60, 2)]
    tables = [_table([["SKU", "Harga"], ["A1", "5000"]], page=2)]
    out = docling_to_parsed(_doc(texts, tables), "application/pdf")
    assert out["tables"][0]["headers"] == ["SKU", "Harga"]
    assert out["tables"][0]["rows"] == [["A1", "5000"]]
    assert out["tables"][0]["page"] == 2
    assert out["is_scanned"] is False


def _xlsx_bytes(rows_per_sheet=3) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Harga"
    ws.append(["SKU", "Harga"])
    for i in range(rows_per_sheet):
        ws.append([f"A{i}", 5000 + i])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_xlsx_tabel_dan_sheet():
    out = await XlsxParser().parse(_xlsx_bytes(), mime=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    assert out["tables"][0]["sheet"] == "Harga"
    assert out["tables"][0]["headers"] == ["SKU", "Harga"]
    assert out["tables"][0]["rows"][0] == ["A0", "5000"]
    assert out["metadata"] == {"sheets": ["Harga"]}
    assert "Harga" in out["pages"][0]["text"]


@pytest.mark.asyncio
async def test_xlsx_batas_sel():
    with pytest.raises(UnsupportedMediaError):
        await XlsxParser(max_cells=2).parse(_xlsx_bytes(5), mime=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))


@pytest.mark.asyncio
async def test_xlsx_macro_ditolak():
    buf = io.BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "spreadsheetml")
        zf.writestr("xl/vbaProject.bin", b"evil")
    with pytest.raises(UnsupportedMediaError):
        await XlsxParser().parse(buf.getvalue(), mime=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))


def _pdf_bytes(image_only: bool = False) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    if image_only:
        pix = pymupdf.Pixmap(pymupdf.Colorspace(pymupdf.CS_RGB),
                             pymupdf.Rect(0, 0, 200, 60), False)
        page.insert_image(pymupdf.Rect(50, 50, 250, 110), pixmap=pix)
    else:
        page.insert_text(
            (72, 72),
            "Teks digital biasa untuk probe. " * 4)  # >50 char/halaman
    return doc.tobytes()


@pytest.mark.asyncio
async def test_probe_digital_vs_pindai():
    digital = await probe_pdf(_pdf_bytes(False))
    assert digital["pages"] == 1
    assert digital["looks_scanned"] is False
    scanned = await probe_pdf(_pdf_bytes(True))
    assert scanned["pages"] == 1
    assert scanned["looks_scanned"] is True
