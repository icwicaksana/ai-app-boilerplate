"""`DocumentParser` via Docling (PDF digital + DOCX + PPTX).

- Lazy import `docling` di method (impor berat ±detik — jangan bebankan boot API;
  modul ini hanya dipakai worker).
- Konversi sync/blocking → `asyncio.to_thread` (jangan bekukan event loop).
- Halaman dari `prov.page_no` (1-based); heading dari label TITLE/SECTION_HEADER.
- `is_scanned`: total teks < 50 char/halaman (D10) → kandidat OCR.
- OCR Docling sendiri DIMATIKAN (arsitektur: Tesseract terpisah, D10).
"""

from __future__ import annotations

import asyncio

OCR_MIN_CHARS_PER_PAGE = 50


def docling_to_parsed(doc, mime: str) -> dict:
    """Petakan DoclingDocument → ParsedDocument (pure mapping, unit-testable)."""
    by_page: dict[int, list[str]] = {}
    headings: dict[int, list[str]] = {}
    for item in doc.texts:
        prov_pages = [int(getattr(pr, "page_no", 1) or 1) for pr in (item.prov or [])]
        page = prov_pages[0] if prov_pages else 1
        text = (item.text or "").strip()
        if not text:
            continue
        # Item lintas-halaman (Docling menggabung paragraf flowing): utuh di
        # halaman pertama + catat span (observability sitasi).
        if len(prov_pages) > 1:
            text = f"{text} [hal. {min(prov_pages)}-{max(prov_pages)}]"
        by_page.setdefault(page, []).append(text)
        label = str(getattr(item, "label", "") or "").upper()
        if "TITLE" in label or "SECTION_HEADER" in label or "HEADER" in label:
            headings.setdefault(page, []).append(text[:200])

    num_pages = doc.num_pages() if hasattr(doc, "num_pages") else 0
    pages = [{"page": p,
              "text": "\n".join(by_page.get(p, [])),
              "headings": headings.get(p, [])}
             for p in range(1, (num_pages or max(by_page or [1])) + 1)]

    tables = []
    for table in doc.tables:
        grid = getattr(getattr(table, "data", None), "grid", None) or []
        rows = [[(c.text or "").strip() for c in row] for row in grid]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        page = None
        if table.prov:
            page = int(getattr(table.prov[0], "page_no", 0) or 0) or None
        tables.append({"page": page, "sheet": None,
                       "headers": rows[0], "rows": rows[1:]})

    total_chars = sum(len(p["text"]) for p in pages)
    denom = max(1, len(pages))
    return {"pages": pages, "tables": tables,
            "is_scanned": (total_chars / denom) < OCR_MIN_CHARS_PER_PAGE,
            "parser": "docling", "metadata": {"mime": mime}}


class DoclingParser:
    parser_name = "docling"
    supported_mimes = frozenset({
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    })

    def __init__(self) -> None:
        import threading

        self._converter = None
        self._lock = threading.Lock()

    def _converter_or_build(self):
        # Double-checked locking: worker multi-task, converter mahal (P1).
        if self._converter is None:
            with self._lock:
                if self._converter is None:
                    from docling.document_converter import DocumentConverter

                    self._converter = DocumentConverter()
        return self._converter

    async def parse(self, blob: bytes, *, mime: str) -> dict:
        if mime not in self.supported_mimes:
            raise ValueError(f"docling tak dukung {mime}")
        return await asyncio.to_thread(self._parse_sync, blob, mime)

    def _parse_sync(self, blob: bytes, mime: str) -> dict:
        from io import BytesIO

        from docling.datamodel.base_models import DocumentStream

        ext = {"application/pdf": "pdf"}.get(
            mime, "docx" if "wordprocessing" in mime else "pptx")
        result = self._converter_or_build().convert(
            DocumentStream(name=f"doc.{ext}", stream=BytesIO(blob)))
        return docling_to_parsed(result.document, mime)
