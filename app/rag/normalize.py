"""Normalisasi teks Indonesia untuk sisi LEXICAL retrieval (v0.7.1 F3).

Masalah nyata: `plainto_tsquery` me-AND-kan SEMUA term — kata tanya
("berapa", "bagaimana") yang tak ada di dokumen membuat lexical KOSONG.
Imbuhan ("diretur" vs "retur") dan angka-kata ("35 ribu" vs "35000") juga
miss di sisi lexical (dense menutupinya, tapi lexical = jaring kedua).

Tahapan (deterministik, idempoten, total — tak pernah raise untuk str):
1. fold NFKD + lowercase,
2. ekspansi angka-skala ("35 ribu" → "35 ribu 35000"; APPEND, bukan ganti —
   cocok dengan dokumen bentuk apa pun),
3. tanda baca → spasi,
4. buang stopword (kata tanya + fungsi; DO NOT STEM daftar ini),
5. stemming Sastrawi per token (opt-out via `stem=False`).

HANYA dipakai sisi lexical (query + kolom `content_norm`). Dense/rerank
tetap teks mentah (embedding justru paham konteks imbuhan).
"""

from __future__ import annotations

import re
import unicodedata

# Kata tanya + kata fungsi Indonesia (konservatif: hanya yang tak pernah
# jadi pembeda makna dokumen). Ditulis lowercase (input sudah di-fold).
STOPWORDS = frozenset("""
apa apakah siapa kapan berapa bagaimana mengapa kenapa mana dimana kemana
yang dan atau di ke dari pada untuk dengan oleh adalah ialah yaitu yakni
ini itu tersebut para setiap tiap sangat lebih kurang bisa dapat akan
sudah telah sedang masih juga hanya saja tidak tak bukan jangan agar
supaya jika kalau bahwa sebagai dalam antara tentang atas bawah saat
ketika sambil serta maupun namun tetapi tapi sedangkan sementara lalu
kemudian maka jadi sehingga bagi demi tanpa selain kecuali hingga sampai
sejak selama sesudah sebelum setelah apabila anda kamu saya kami kita
mereka dia ia nya lah kah pun per hal banyak beberapa semua seluruh
""".split())

_SCALES = (("triliun", 10**12), ("miliar", 10**9), ("milyar", 10**9),
           ("juta", 10**6), ("ribu", 10**3))
_SCALE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(triliun|miliar|milyar|juta|ribu)\b")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]", re.ASCII)

_stemmer = None


def _get_stemmer():
    """Singleton lazy (impor Sastrawi hanya saat stemming dipakai)."""
    global _stemmer
    if _stemmer is None:
        from Sastrawi.Stemmer.StemmerFactory import StemmerFactory

        _stemmer = StemmerFactory().create_stemmer()
    return _stemmer


def expand_number_scales(text: str) -> str:
    """'35 ribu' → '35 ribu 35000'; '1,5 juta' → '1,5 juta 1500000'.

    Idempoten: lewati bila hasil ekspansi sudah tepat setelah match
    ("35 ribu 35000" → utuh).
    """
    out: list[str] = []
    pos = 0
    mults = dict(_SCALES)
    for m in _SCALE_RE.finditer(text):
        raw = m.group(1).replace(",", ".")
        try:
            expanded = str(int(float(raw) * mults[m.group(2)]))
        except ValueError:
            continue
        out.append(text[pos:m.end()])
        pos = m.end()
        # Sudah diekspansi sebelumnya? → jangan gandakan.
        tail = text[pos:pos + len(expanded) + 1]
        if tail != f" {expanded}":
            out.append(f" {expanded}")
    out.append(text[pos:])
    return "".join(out)


def normalize_id(text: str, *, stem: bool = True) -> str:
    """Normalisasi penuh untuk lexical. Selalu return str (tak pernah raise)."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text).encode(
        "ascii", "ignore").decode("ascii").lower()
    folded = expand_number_scales(folded)
    folded = _PUNCT_RE.sub(" ", folded)
    tokens = [t for t in folded.split()
              if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]
    if stem and tokens:
        stemmer = _get_stemmer()
        tokens = [stemmer.stem(t) for t in tokens]
    return " ".join(tokens)
