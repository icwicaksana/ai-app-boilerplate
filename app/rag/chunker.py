"""Chunker RAG (D8, beku): 4 preset + satuan token BGE-M3.

Preset (ukuran/overlap dalam TOKEN):
- `prose`: 512/64, batas paragraf (fallback kalimat, lalu kata).
- `markdown`: 512/64, header-aware (split H2/H3; `headers[]` ke metadata).
- `pdf`: 512/64, preservasi batas halaman (`page` per chunk, tak lintas halaman).
- `factoid`: 256/0, 1 baris = 1 chunk (SKU/harga/spesifikasi).

Murni: tokenizer di-inject (`TokenizerPort`). Overlap = kalimat/kata ekor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.ports import TokenizerPort

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MD_HEAD_RE = re.compile(r"^(#{2,3})\s+(.+)$")
# H1 (# ...) SENGAJA bukan pemisah seksi: H1 = judul dokumen (1x, jadi isi);
# seksi = H2/H3. Keputusan sadar (bukan kelupaan).


@dataclass
class Chunk:
    content: str
    ord: int
    page: int | None = None
    sheet: str | None = None
    heading: str | None = None
    headers: list[str] = field(default_factory=list)
    token_count: int = 0


PRESETS: dict[str, dict[str, int]] = {
    "prose": {"size": 512, "overlap": 64},
    "markdown": {"size": 512, "overlap": 64},
    "pdf": {"size": 512, "overlap": 64},
    "factoid": {"size": 256, "overlap": 0},
}

# Versi logika chunker — NAIKKAN tiap ubah PRESETS/split/prefix (masuk
# `chunk_id` via preset_version; ID lama = teks lama, F-5).
CHUNKER_VERSION = "v1"


def _sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_SPLIT_RE.split(text or "") if s.strip()]
    if not parts and (text or "").strip():
        return [(text or "").strip()]
    return parts


def _words(text: str) -> list[str]:
    return (text or "").split()


def _pack(units: list[str], size: int, overlap: int,
          tok: TokenizerPort) -> list[str]:
    """Gabung unit (kalimat/paragraf) menjadi chunk ≤size + overlap ekor."""
    out: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for u in units:
        t = tok.count(u)
        if t > size:
            # Unit raksasa: FLUSH buffer dulu (jaga urutan baca, F-6),
            # lalu potong per kata + overlap kata.
            if cur:
                out.append(" ".join(cur))
            words = _words(u)
            i = 0
            carry: list[str] = []
            while i < len(words):
                buf, btok = list(carry), sum(
                    tok.count(w) for w in carry)
                while i < len(words) and btok + tok.count(words[i]) <= size:
                    buf.append(words[i])
                    btok += tok.count(words[i])
                    i += 1
                if not buf:  # 1 kata > size (tak mungkin praktis): paksa maju
                    buf, i = [words[i]], i + 1
                out.append(" ".join(buf))
                # Overlap: kata ekor ≤ budget.
                carry, ctok = [], 0
                for w in reversed(buf):
                    wt = tok.count(w)
                    if overlap and ctok + wt <= overlap:
                        carry.append(w)
                        ctok += wt
                    else:
                        break
                carry.reverse()
            cur, cur_tok = [], 0
            continue
        if cur and cur_tok + t > size:
            out.append(" ".join(cur))
            # Overlap: kalimat ekor ≤ overlap budget.
            tail, ttok = [], 0
            for s in reversed(cur):
                st = tok.count(s)
                if ttok + st > overlap:
                    break
                tail.append(s)
                ttok += st
            cur, cur_tok = list(reversed(tail)), ttok
        cur.append(u)
        cur_tok += t
    if cur:
        out.append(" ".join(cur))
    return [c for c in out if c.strip()]


def chunk_prose(text: str, preset: str, tok: TokenizerPort,
                *, page: int | None = None,
                heading: str | None = None,
                headers: list[str] | None = None,
                start_ord: int = 0) -> list[Chunk]:
    """Chunk generik: paragraf → kalimat. Dipakai prose/pdf-per-halaman."""
    cfg = PRESETS[preset]
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    units: list[str] = []
    for p in paras:
        units.extend(_sentences(p))
    texts = _pack(units, cfg["size"], cfg["overlap"], tok)
    return [Chunk(content=t, ord=start_ord + i, page=page, heading=heading,
                  headers=list(headers or []),
                  token_count=tok.count(t)) for i, t in enumerate(texts)]


def chunk_markdown(text: str, tok: TokenizerPort) -> list[Chunk]:
    """Header-aware: kumpulkan per seksi H2/H3, bawa konteks header."""
    cfg = PRESETS["markdown"]
    sections: list[tuple[list[str], list[str]]] = []
    cur_headers: list[str] = []
    cur_body: list[str] = []

    def flush() -> None:
        if cur_body:
            sections.append((list(cur_headers), list(cur_body)))

    for line in (text or "").splitlines():
        m = _MD_HEAD_RE.match(line.strip())
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            cur_headers = [h for h in cur_headers]  # salin
            # Sesuaikan kedalaman: H2 reset, H3 menempel di bawah H2 terakhir.
            if level <= 2:
                cur_headers = [title]
            else:
                cur_headers = (cur_headers[:1] + [title]) if cur_headers else [title]
            cur_body = []
        elif line.strip():
            cur_body.append(line.strip())
    flush()
    if not sections and (text or "").strip():
        sections.append(([], [(text or "").strip()]))

    chunks: list[Chunk] = []
    for headers, body in sections:
        prefix = " > ".join(headers)
        # Anggaran prefix DIKURANGKAN dari budget pack agar total ≤ size
        # (F-7: dulu prefix ditempel setelah pack = jebol 512).
        budget = cfg["size"] - (tok.count(f"[{prefix}] ") if prefix else 0)
        budget = max(64, budget)
        units: list[str] = []
        for para in body:
            units.extend(_sentences(para))
        for t in _pack(units, budget, cfg["overlap"], tok):
            # Konteks header di-prefix (murah, menaikkan lexical).
            content = f"[{prefix}] {t}" if prefix else t
            chunks.append(Chunk(content=content, ord=len(chunks),
                                heading=headers[-1] if headers else None,
                                headers=list(headers),
                                token_count=tok.count(content)))
    return chunks


def chunk_pages(pages: list[dict], tok: TokenizerPort) -> list[Chunk]:
    """Preset `pdf`: chunk per halaman (tak pernah lintas halaman)."""
    chunks: list[Chunk] = []
    for p in pages:
        for c in chunk_prose(str(p.get("text", "")), "pdf", tok,
                             page=p.get("page"), start_ord=len(chunks)):
            chunks.append(c)
    for i, c in enumerate(chunks):
        c.ord = i
    return chunks


def chunk_factoid_lines(lines: list[str], tok: TokenizerPort) -> list[Chunk]:
    """Preset `factoid`: 1 baris = 1 chunk (256 tok, tanpa overlap)."""
    chunks: list[Chunk] = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        if tok.count(line) > PRESETS["factoid"]["size"]:
            line = tok.truncate_words(line, PRESETS["factoid"]["size"])
        chunks.append(Chunk(content=line, ord=len(chunks),
                            token_count=tok.count(line)))
    return chunks


def chunk_text(text: str, preset: str, tok: TokenizerPort) -> list[Chunk]:
    """Dispatcher preset untuk teks polos (markdown/factoid punya jalur sendiri)."""
    if preset == "markdown":
        return chunk_markdown(text, tok)
    if preset not in PRESETS:
        raise ValueError(f"preset tak dikenal: {preset} "
                         f"(izinkan: {sorted(PRESETS)})")
    return chunk_prose(text, preset, tok)
