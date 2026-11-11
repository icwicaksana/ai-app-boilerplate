"""Sniff MIME dari magic number (BUKAN ekstensi) — stdlib saja.

Alasan: ekstensi bisa dibohongi (evil.pdf.exe). Worker menolak
`application/octet-stream` / zip tak dikenal dengan UNSUPPORTED_MEDIA (D10).
"""

from __future__ import annotations

import io
import zipfile

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MD = "text/markdown"
TXT = "text/plain"
UNKNOWN = "application/octet-stream"

_OFFICE_MARKERS = (
    ("spreadsheetml", XLSX),
    ("presentationml", PPTX),
    ("wordprocessingml", DOCX),
)


def _is_text(blob: bytes) -> bool:
    if not blob or b"\x00" in blob[:8192]:
        return False
    try:
        blob[:65536].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def sniff_mime(blob: bytes, filename: str = "") -> str:
    """Tebak MIME dari ISI. `filename` hanya untuk MD-vs-TXT."""
    if blob.startswith(b"%PDF-"):
        return PDF
    if blob.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                types = zf.read("[Content_Types].xml").decode("utf-8",
                                                              errors="ignore")
            for marker, mime in _OFFICE_MARKERS:
                if marker in types:
                    return mime
        except Exception:  # noqa: BLE001 - zip rusak/asing = unknown
            pass
        return UNKNOWN
    if _is_text(blob):
        ext = (filename.rsplit(".", 1)[-1].lower()
               if "." in (filename or "") else "")
        return MD if ext in ("md", "markdown") else TXT
    return UNKNOWN


def zip_has_macro(blob: bytes) -> bool:
    """True bila arsip Office mengandung VBA (tolak eksplisit, D10)."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
    except Exception:  # noqa: BLE001 - bukan zip valid
        return False
    return any("vbaProject.bin" in n or n.endswith(".bin") and "vba" in n.lower()
               for n in names)
