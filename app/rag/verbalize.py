"""Verbalisasi path graph → kalimat natural (murni, tanpa LLM/model).

Aturan (DESIGN §4 + G5):
- 1 triple = 1 kalimat mandiri dengan subjek eksplisit; DILARANG menggabung
  2 hop jadi satu klaim transitivitas ("A terkait B, B terkait C" ≠ "A–C").
- Template fixed-set per relasi core; ekstensi `tenant:*` pakai fallback aman.
- Output = untrusted (disanitasi di sini + `sanitize_untrusted` di grounding).
- Maks 2 triple (bounded 2-hop); lebih = potong defensif.
"""

from __future__ import annotations

import re

from app.rag.ontology import TENANT_PREFIX

_TEMPLATES = {
    "memasok": "{s} memasok {o}.",
    "dipakai_di": "{s} dipakai di {o}.",
    "bagian_dari": "{s} adalah bagian dari {o}.",
    "berlokasi_di": "{s} berlokasi di {o}.",
    "bekerja_sebagai": "{s} bekerja sebagai {o}.",
    "memiliki": "{s} memiliki {o}.",
    "menerbitkan": "{s} menerbitkan {o}.",
    "berlaku_sejak": "{s} berlaku sejak {o}.",
    "berlaku_sampai": "{s} berlaku sampai {o}.",
    "mensyaratkan": "{s} mensyaratkan {o}.",
    "melarang": "{s} melarang {o}.",
    "menggantikan": "{s} menggantikan {o}.",
    "terkait_dengan": "{s} terkait dengan {o}.",
    "related_to": "{s} terkait dengan {o}.",
    "menyebut": "{s} menyebut {o}.",
}

_WS_RE = re.compile(r"\s+")
_BAD_RE = re.compile(r"[<>]{2,}")  # blokir marker <<< >>>
_REL_SAFE_RE = re.compile(r"[^a-z0-9_:]")

MAX_TRIPLES = 2
MAX_LABEL = 160  # per label; lebih = potong (hemat budget pair rerank)


def _clean_label(text: str) -> str:
    text = _BAD_RE.sub("", text or "")
    text = _WS_RE.sub(" ", text).strip()
    return text[:MAX_LABEL]


def _render_relation(relation: str) -> str:
    if relation in _TEMPLATES:
        return relation
    if relation.startswith(TENANT_PREFIX):  # ekstensi: pakai apa adanya (aman)
        return _REL_SAFE_RE.sub("", relation[len(TENANT_PREFIX):]) or "terkait"
    return "terkait"


def verbalize_path(triples: list[tuple[str, str, str]]) -> str:
    """Render ≤2 triple jadi 1–2 kalimat. Tak pernah raise untuk input str."""
    out: list[str] = []
    for src, rel, dst in triples[:MAX_TRIPLES]:
        s, o = _clean_label(src), _clean_label(dst)
        if not s or not o:
            continue
        key = rel if rel in _TEMPLATES else None
        if key is not None:
            out.append(_TEMPLATES[key].format(s=s, o=o))
        else:
            out.append(f"{s} {_render_relation(rel)} {o}.")
    return " ".join(out)
