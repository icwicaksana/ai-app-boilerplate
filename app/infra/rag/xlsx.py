"""`DocumentParser` XLSX via openpyxl (read-only, tanpa macro).

- Macro (`.xlsm` / `vbaProject.bin`) DITOLAK eksplisit (D10).
- Guard `MAX_XLSX_CELLS` SEBELUM baca penuh (streaming read-only).
- Tiap sheet → 1 `ParsedTable` (baris pertama = header) + ringkasan halaman.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

from app.core.errors import UnsupportedMediaError
from app.rag.parsers.sniff import XLSX, zip_has_macro


class XlsxParser:
    parser_name = "xlsx"
    supported_mimes = frozenset({XLSX})

    def __init__(self, max_cells: int = 200_000) -> None:
        self._max_cells = max_cells

    async def parse(self, blob: bytes, *, mime: str) -> dict:
        if mime not in self.supported_mimes:
            raise ValueError(f"xlsx tak dukung {mime}")
        if zip_has_macro(blob):
            raise UnsupportedMediaError(
                "Berkas mengandung macro/VBA — ditolak eksplisit (D10).")
        return await asyncio.to_thread(self._parse_sync, blob)

    def _parse_sync(self, blob: bytes) -> dict:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True,
                           keep_vba=False)
        try:
            names = list(wb.sheetnames)
            # Guard sel: dimension bisa dibohongi — hitung cepat per sheet
            # (iter_rows values_only, berhenti saat batas terlampaui).
            tables, summaries = [], []
            for sheet in names:
                ws = wb[sheet]
                rows: list[list[str]] = []
                cells = 0
                for row in ws.iter_rows(values_only=True):
                    vals = ["" if v is None else str(v).strip() for v in row]
                    if not any(vals):
                        continue
                    cells += len(vals)
                    if cells > self._max_cells:
                        raise UnsupportedMediaError(
                            f"Sheet '{sheet}' melebihi {self._max_cells} sel.")
                    rows.append(vals)
                if not rows:
                    continue
                tables.append({"page": None, "sheet": sheet,
                               "headers": rows[0], "rows": rows[1:]})
                summaries.append(
                    f"[Sheet: {sheet}] {len(rows) - 1} baris data, "
                    f"kolom: {', '.join(rows[0][:12])}")
            text = "\n".join(summaries)
            return {"pages": [{"page": 1, "text": text, "headings": names}],
                    "tables": tables, "is_scanned": False,
                    "parser": self.parser_name,
                    "metadata": {"sheets": names}}
        finally:
            wb.close()
