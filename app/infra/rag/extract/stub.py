"""Ekstraktor stub deterministik (F2): lifecycle hijau tanpa model/LLM.

Menemukan pola "X dipasok Y" / "X berlokasi di Y" via regex + kata
kapital sebagai entity kandidat. HANYA untuk F2 (uji lifecycle
end-to-end); F4 mengganti dengan L1/L2/L3 nyata di belakang flag.
"""

from __future__ import annotations

import re

from app.rag.normalize import normalize_entity
from app.rag.ports import Extraction

_SEG = r"([^.\n]+?)"
_PATTERNS = (
    # "X dipasok Y" ⟺ "Y memasok X" (bukan dipakai_di — semantik relasi!).
    (re.compile(_SEG + r"\s+dipasok\s+" + _SEG + r"(?:\.|$)"), "memasok",
     True),
    (re.compile(_SEG + r"\s+memasok\s+" + _SEG + r"(?:\.|$)"), "memasok",
     False),
    (re.compile(_SEG + r"\s+berlokasi di\s+" + _SEG + r"(?:\.|$)"),
     "berlokasi_di", False),
)
_CAP_RE = re.compile(r"\b[A-ZÀ-Ž][\w'.-]*(?:\s+[A-ZÀ-Ž][\w'.-]*){0,4}")


def _clean(name: str) -> str:
    name = name.strip().strip(".,;:").strip()
    return name[:64]


class StubExtractor:
    """`EntityExtractor` deterministik (tanpa model, tanpa I/O)."""

    @property
    def model(self) -> str:
        return "stub-deterministik"

    @property
    def version(self) -> str:
        return "f2"

    async def extract(self, texts: list[str]) -> list[Extraction]:
        return [self._one(t or "") for t in texts]

    def _one(self, text: str) -> Extraction:
        entities: dict[str, dict] = {}
        edges: list[dict] = []

        def _add(name: str, type: str = "lain") -> str:
            norm = normalize_entity(name)
            if norm and norm not in entities:
                entities[norm] = {
                    "name": name, "norm": norm, "type": type,
                    "confidence": 1.0, "chunk_ord": 0, "chunk_id": "",
                }
            return norm

        for pat, rel, reverse in _PATTERNS:
            for m in pat.finditer(text):
                a, b = _clean(m.group(1)), _clean(m.group(2))
                if not a or not b:
                    continue
                na, nb = _add(a, "organisasi"), _add(b, "produk")
                src, dst = (nb, na) if reverse else (na, nb)
                edges.append({
                    "src_norm": src, "dst_norm": dst, "relation": rel,
                    "confidence": 1.0, "layer": "L1",
                    "coref_resolved": True, "access_level": "public",
                    "chunk_ord": 0, "chunk_id": "", "chunk_row_id": None,
                })
        for m in _CAP_RE.finditer(text):
            name = _clean(m.group(0))
            if len(name) >= 3 and normalize_entity(name) not in entities:
                _add(name)
        return {"entities": list(entities.values()), "edges": edges}
