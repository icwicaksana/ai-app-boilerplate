"""Sitasi RAG (D7, beku): ID stabil + marker [^n] + render manusia.

- `chunk_id` stabil = sha256(doc_id:seq:preset_version) (F-08).
- `reply` memakai marker `[^n]`; `output.citations[n]` mesin-terbaca.
- Test AC#3: setiap `[^n]` wajib punya entri sitasi + chunk_id ada di retrieval-set.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_MARKER_RE = re.compile(r"\[\^(\d+)\]")


def chunk_id(doc_id: str, seq: int | str,
             preset_version: str = "v1") -> str:
    """ID chunk stabil lintas re-ingest (ganti preset = versi baru).

    `seq` int untuk chunk teks (ord global); str namespaced
    (`tbl:{sheet}:{rowhash8}`) untuk baris tabel — stabil terhadap
    penyisipan paragraf (F-5). `preset_version` WAJIB menyertakan preset
    aktif (`v1:{preset}`) + `CHUNKER_VERSION` (K5/P5).
    """
    raw = f"{doc_id}:{seq}:{preset_version}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def gpath_id(edge_ids: list[int], preset_version: str = "gv1") -> str:
    """ID pseudo-chunk graph stabil (v0.8 D8).

    `sorted(edge_ids)` = invarian urutan BFS; `preset_version` = versi
    template verbalize (ganti template = ID baru, pola `chunk_id`).
    """
    raw = f"gpath:{','.join(str(i) for i in sorted(edge_ids))}:{preset_version}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def extract_markers(reply: str) -> list[int]:
    """Nomor sitasi yang dirujuk di teks jawaban, urut kemunculan."""
    return [int(m.group(1)) for m in _MARKER_RE.finditer(reply or "")]


def build_citations(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bangun daftar sitasi mesin dari retrieval-set (n = 1-based)."""
    out: list[dict[str, Any]] = []
    for i, c in enumerate(chunks, start=1):
        meta = c.get("metadata") or {}
        out.append({
            "n": i,
            "doc_id": c.get("doc_id", ""),
            "chunk_id": c.get("chunk_id", ""),
            "source": c.get("source", ""),
            "page": c.get("page"),
            "sheet": c.get("sheet") or meta.get("sheet"),
            "heading": c.get("heading"),
            "headers": meta.get("headers", []),
            "score": float(c.get("score", 0.0)),
            "backend": c.get("backend", ""),
        })
    return out


def filter_cited(citations: list[dict[str, Any]],
                 reply: str) -> list[dict[str, Any]]:
    """Sempitkan sitasi ke yang BENAR-BENAR disitasi reply (F-10).

    Mencegah kepercayaan berlebih (5 sumber padahal 1 disitasi).
    """
    used = set(extract_markers(reply))
    return [c for c in citations if c.get("n") in used]


def validate_markers(reply: str, citations: list[dict[str, Any]]) -> list[str]:
    """Kembalikan daftar masalah (kosong = valid): marker tanpa entri sitasi."""
    problems: list[str] = []
    valid = {c.get("n") for c in citations}
    for n in extract_markers(reply):
        if n not in valid:
            problems.append(f"marker [^{n}] tanpa entri sitasi")
    return problems


def render_sources(citations: list[dict[str, Any]]) -> str:
    """Render blok 'Sumber:' manusiawi untuk akhir jawaban.

    Short-id 8 char untuk verifikasi manusia vs `chunk_id` mesin (F-5).
    """
    lines = []
    for c in citations:
        loc = c["source"] or c["doc_id"]
        if c.get("page") is not None:
            loc += f" (hal. {c['page']})"
        elif c.get("sheet"):
            loc += f" (sheet {c['sheet']})"
        short = str(c.get("chunk_id") or "")[:8]
        lines.append(f"[^{c['n']}] {loc} · {short}")
    return "\n".join(lines)
