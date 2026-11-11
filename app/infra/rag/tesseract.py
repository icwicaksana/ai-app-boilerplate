"""`OCREngine` via Tesseract (D10): raster pymupdf (DPI 300) + pytesseract.

- Binary `tesseract` + data `ind+eng` = prasyarat sistem (Dockerfile apt;
  Windows = install manual, didokumentasikan). Tak ada binary → raise jelas.
- Timeout per halaman (pytesseract `timeout`) + batas halaman.
- Confidence 0..1 dari `image_to_data` (rata-rata conf valid).
"""

from __future__ import annotations

import asyncio
import io
import shutil

from app.core.errors import OcrFailedError


class TesseractEngine:
    engine_name = "tesseract"

    def __init__(self, dpi: int = 300) -> None:
        self._dpi = dpi

    async def ocr(self, pdf_bytes: bytes, *, langs: str,
                  timeout_seconds: int, max_pages: int) -> dict:
        if shutil.which("tesseract") is None:
            raise OcrFailedError(
                "Binary tesseract tak ditemukan. Install: Windows "
                "(https://github.com/UB-Mannheim/tesseract/wiki + paket "
                "ind+eng) atau image worker (apt tesseract-ocr-ind/eng).")
        return await asyncio.to_thread(
            self._ocr_sync, pdf_bytes, langs, timeout_seconds, max_pages)

    def _ocr_sync(self, pdf_bytes: bytes, langs: str,
                  timeout_seconds: int, max_pages: int) -> dict:
        import pymupdf
        from pytesseract import Output, image_to_data

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise OcrFailedError(f"PDF tak terbaca ({exc}).") from exc
        if len(doc) > max_pages:
            raise OcrFailedError(
                f"PDF {len(doc)} halaman > batas OCR {max_pages}.")
        texts, confs, pages = [], [], []
        with doc:
            for i, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=self._dpi)
                img = io.BytesIO(pix.tobytes("png"))
                try:
                    from PIL import Image

                    data = image_to_data(
                        Image.open(img), lang=langs, timeout=timeout_seconds,
                        output_type=Output.DICT)
                except RuntimeError as exc:
                    raise OcrFailedError(
                        f"OCR timeout halaman {i} ({timeout_seconds}s).") from exc
                words = [w for w in data.get("text", []) if (w or "").strip()]
                page_text = " ".join(words)
                texts.append(page_text)
                for conf_raw, word in zip(data.get("conf", []),
                                          data.get("text", [])):
                    if not str(word or "").strip():
                        continue
                    try:
                        conf = float(conf_raw)
                    except (TypeError, ValueError):
                        continue
                    if conf >= 0:
                        confs.append(conf)
                pages.append({"page": i, "text": page_text, "boxes": []})
        mean_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return {"text": "\n".join(texts), "confidence": mean_conf,
                "pages": pages, "lang": langs}
