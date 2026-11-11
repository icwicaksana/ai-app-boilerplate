"""Unit sniff + parser Markdown (murni, tanpa I/O)."""

import io
import zipfile

import pytest

from app.rag.parsers.markdown import MarkdownParser, parse_markdown
from app.rag.parsers.sniff import sniff_mime, zip_has_macro


def _office_zip(marker: str, macro: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml",
                    f"<Types><x val='{marker}'/></Types>")
        if macro:
            zf.writestr("xl/vbaProject.bin", b"macro-bytes")
    return buf.getvalue()


def test_sniff_pdf():
    assert sniff_mime(b"%PDF-1.7 ...") == "application/pdf"


def test_sniff_office_dari_content_types():
    assert sniff_mime(_office_zip("wordprocessingml")).startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert sniff_mime(_office_zip("spreadsheetml")).endswith("sheet")
    assert sniff_mime(_office_zip("presentationml")).endswith("presentation")


def test_sniff_zip_asing_unknown():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "halo")
    assert sniff_mime(buf.getvalue()) == "application/octet-stream"


def test_sniff_teks_md_vs_txt():
    assert sniff_mime("# Halo".encode(), "a.md") == "text/markdown"
    assert sniff_mime("# Halo".encode(), "a.txt") == "text/plain"
    assert sniff_mime("# Halo".encode(), "") == "text/plain"
    assert sniff_mime(b"\x89PNG\r\n\x1a....") == "application/octet-stream"


def test_zip_macro_terdeteksi():
    assert zip_has_macro(_office_zip("spreadsheetml", macro=True)) is True
    assert zip_has_macro(_office_zip("spreadsheetml")) is False
    assert zip_has_macro(b"bukan zip") is False


def test_md_headings_dan_tabel():
    doc = parse_markdown("## Retur\n\n| SKU | Harga |\n|---|---|\n| A1 | 5000 |\n")
    assert doc["pages"][0]["headings"] == ["Retur"]
    assert doc["tables"][0]["headers"] == ["SKU", "Harga"]
    assert doc["tables"][0]["rows"] == [["A1", "5000"]]
    assert doc["is_scanned"] is False


def test_md_tanpa_tabel():
    doc = parse_markdown("Halo dunia.")
    assert doc["tables"] == []
    assert doc["pages"][0]["text"] == "Halo dunia."


@pytest.mark.asyncio
async def test_markdown_parser_protocol():
    p = MarkdownParser()
    assert p.parser_name == "markdown"
    assert "text/markdown" in p.supported_mimes
    doc = await p.parse("# T\nisi".encode(), mime="text/markdown")
    assert doc["parser"] == "markdown"
    assert doc["metadata"] == {"format": "md"}
    doc2 = await p.parse(b"\xff\xfe invalid utf8 \x00?".replace(b"\x00", b""),
                         mime="text/plain")
    assert doc2["pages"][0]["text"]  # fallback replace, tak raise
