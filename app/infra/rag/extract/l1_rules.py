"""L1 deterministik (gratis): pola relasi eksplisit Bahasa Indonesia.

Cakupan: kalimat berpola "[X] <verba-relasi> [Y]" + baris factoid tabel
("[Sheet] h1: v1 | h2: v2" → edge per sel non-kosong ke kunci baris).
Confidence SELALU 1.0 (aturan, bukan tebakan). Tanpa model, tanpa I/O.
Dipakai langsung (teks umum) + oleh `LayeredExtractor` sebagai lapis 1.
"""

from __future__ import annotations

import re

from app.rag.normalize import normalize_entity
from app.rag.ports import Extraction

# (pola, relasi, balik_subjek_objek)
# Span entity = [^.\n] (TAK PERNAH lintas kalimat — pelajaran F5 run 3:
# `.+?` menelan 4 kalimat + kepotong tengah kata → "pt sumbe").
_SEG = r"([^.\n]+?)"
_PATTERNS = (
    (re.compile(_SEG + r"\s+dipasok\s+" + _SEG + r"(?:\.|$)"), "memasok",
     True),
    (re.compile(_SEG + r"\s+memasok\s+" + _SEG + r"(?:\.|$)"), "memasok",
     False),
    (re.compile(_SEG + r"\s+dipakai di\s+" + _SEG + r"(?:\.|$)"),
     "dipakai_di", False),
    (re.compile(_SEG + r"\s+berlokasi di\s+" + _SEG + r"(?:\.|$)"),
     "berlokasi_di", False),
    (re.compile(_SEG + r"\s+adalah bagian dari\s+" + _SEG + r"(?:\.|$)"),
     "bagian_dari", False),
    (re.compile(_SEG + r"\s+bekerja sebagai\s+" + _SEG + r"(?:\.|$)"),
     "bekerja_sebagai", False),
    (re.compile(_SEG + r"\s+menerbitkan\s+" + _SEG + r"(?:\.|$)"),
     "menerbitkan", False),
    (re.compile(_SEG + r"\s+berlaku sejak\s+" + _SEG + r"(?:\.|$)"),
     "berlaku_sejak", False),
    (re.compile(_SEG + r"\s+berlaku sampai\s+" + _SEG + r"(?:\.|$)"),
     "berlaku_sampai", False),
)

_MAX_SPAN = 80  # span lebih panjang = bukan entity (tolak, bukan potong)

# Baris factoid tabel: "[X] h1: v1 | h2: v2" (lihat table_to_factoid).
_CELL_RE = re.compile(r"([^:|\[\]]+?)\s*:\s*([^|]+)")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")


def _clean(name: str) -> str:
    name = _BRACKET_RE.sub(" ", name or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".,;:").strip()
    if len(name) > _MAX_SPAN or not name:
        return ""
    return name


class L1RulesExtractor:
    """`EntityExtractor` L1 (aturan murni)."""

    model = "l1-rules-id"
    version = "v1"

    async def extract(self, texts: list[str]) -> list[Extraction]:
        return [self._one(t or "") for t in texts]

    def _one(self, text: str) -> Extraction:
        entities: dict[str, dict] = {}
        edges: list[dict] = []

        def _add(name: str, type: str) -> str:
            norm = normalize_entity(name)
            if norm and norm not in entities:
                entities[norm] = {
                    "name": name, "norm": norm, "type": type,
                    "confidence": 1.0, "chunk_ord": 0, "chunk_id": "",
                }
            return norm

        def _edge(src: str, dst: str, rel: str) -> None:
            if src and dst and src != dst:
                edges.append({
                    "src_norm": src, "dst_norm": dst, "relation": rel,
                    "confidence": 1.0, "layer": "L1",
                    "coref_resolved": True, "access_level": "public",
                    "chunk_ord": 0, "chunk_id": "", "chunk_row_id": None,
                })

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line and ":" in line:
                self._factoid_line(line, _add, _edge)
                continue
            for pat, rel, reverse in _PATTERNS:
                for m in pat.finditer(line):
                    a, b = _clean(m.group(1)), _clean(m.group(2))
                    if not a or not b:
                        continue
                    na, nb = _add(a, "lain"), _add(b, "lain")
                    s, o = (nb, na) if reverse else (na, nb)
                    _edge(s, o, rel)
        return {"entities": list(entities.values()), "edges": edges}

    def _factoid_line(self, line: str, _add, _edge) -> None:
        """Baris tabel → edge tiap sel ke entitas kunci (kolom pertama)."""
        cells = [(k.strip(), _clean(v))
                 for k, v in _CELL_RE.findall(line)]
        cells = [(k, v) for k, v in cells if k and v]
        if len(cells) < 2:
            return
        key = _add(cells[0][1], "lain")
        if not key:
            return
        for header, value in cells[1:]:
            node = _add(value, "lain")
            if node and node != key:
                _edge(key, node, "terkait_dengan")
