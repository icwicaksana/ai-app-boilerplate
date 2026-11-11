"""Parser Markdown/TXT murni → `ParsedDocument`.

- 1 halaman logis (page=1); chunker `markdown` yang split header-aware.
- Tabel pipe (`| a | b |`) diekstrak → `ParsedTable` (baris = fakta).
- `is_scanned` selalu False (teks asli).
"""

from __future__ import annotations

import re

from app.rag.ports import ParsedDocument, ParsedPage, ParsedTable

_MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*\|?\s*$")


def _split_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def _extract_tables(lines: list[str]) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    i = 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _MD_TABLE_SEP_RE.match(lines[i + 1]):
            headers = _split_row(lines[i])
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(_split_row(lines[j]))
                j += 1
            if rows:
                tables.append({"page": 1, "sheet": None, "headers": headers,
                               "rows": rows})
            i = j
        else:
            i += 1
    return tables


def parse_markdown(text: str) -> ParsedDocument:
    """Parse MD/TXT. Tak pernah raise untuk input teks (fallback = 1 halaman)."""
    text = text or ""
    lines = text.splitlines()
    headings = []
    for line in lines:
        m = _MD_HEAD_RE.match(line.strip())
        if m:
            headings.append(m.group(2).strip())
    page: ParsedPage = {"page": 1, "text": text, "headings": headings}
    return {"pages": [page], "tables": _extract_tables(lines),
            "is_scanned": False, "parser": "markdown",
            "metadata": {"format": "md"}}


class MarkdownParser:
    """`DocumentParser` untuk text/markdown + text/plain."""

    parser_name = "markdown"
    supported_mimes = frozenset({"text/markdown", "text/plain"})

    async def parse(self, blob: bytes, *, mime: str) -> ParsedDocument:
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            text = blob.decode("utf-8", errors="replace")
        doc = parse_markdown(text)
        doc["metadata"] = {"format": "md" if mime == "text/markdown" else "txt"}
        return doc
