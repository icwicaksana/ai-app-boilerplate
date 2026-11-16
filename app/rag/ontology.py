"""Ontologi-ringan KG v0.8 (murni, tanpa I/O).

Core beku `kg_onto/v1`: 10 tipe node + 15 relasi (DESIGN §5).
Ekstensi workspace: namespace `workspace:<nama>` — terdaftar + tervalidasi,
TIDAK BOLEH menimpa core. Validasi dipakai worker (tulis) dan adapter
(baca); force-fit ke `related_to` dilacak sebagai metrik ingest.
"""

from __future__ import annotations

ONTO_VERSION = "kg_onto/v1"

NODE_TYPES = frozenset({
    "orang", "organisasi", "produk", "lokasi", "dokumen",
    "tanggal", "harga", "kebijakan", "acara", "lain",
})

RELATIONS = frozenset({
    "memasok", "dipakai_di", "bagian_dari", "berlokasi_di",
    "bekerja_sebagai", "memiliki", "menerbitkan", "berlaku_sejak",
    "berlaku_sampai", "mensyaratkan", "melarang", "menggantikan",
    "terkait_dengan", "related_to", "menyebut",
})

ESCAPE_RELATION = "related_to"
# v0.8.2: prefix kanonis = "workspace:". Data lama tetap TERBACA lewat
# LEGACY_EXT_PREFIXES sampai backfill selesai; tulis selalu kanonis lewat
# canonical_extension(). Inilah satu-satunya tempat literal lama boleh
# muncul di app/ (allowlist guard sempit).
WORKSPACE_EXT_PREFIX = "workspace:"
LEGACY_EXT_PREFIXES = ("tenant:",)
EXT_PREFIXES = (WORKSPACE_EXT_PREFIX, *LEGACY_EXT_PREFIXES)


def is_core_node_type(t: str) -> bool:
    return t in NODE_TYPES


def is_core_relation(r: str) -> bool:
    return r in RELATIONS


def extension_suffix(name: str) -> str | None:
    """Suffix ekstensi (tanpa prefix) atau None bila bukan ekstensi valid."""
    for p in EXT_PREFIXES:
        if name.startswith(p):
            rest = name[len(p):]
            if rest and " " not in rest and "/" not in rest:
                return rest
            return None
    return None


def is_workspace_extension(name: str) -> bool:
    """`workspace:xyz` (atau prefix legacy) valid."""
    return extension_suffix(name) is not None


def canonical_extension(name: str) -> str:
    """Normalkan prefix lama -> kanonis. Non-ekstensi dikembalikan apa adanya.

    Dipakai di batas TULIS supaya data baru tidak pernah tersimpan dengan
    prefix lama (mencegah duplikat antara prefix legacy dan kanonis).
    """
    suffix = extension_suffix(name)
    if suffix is None:
        return name
    return f"{WORKSPACE_EXT_PREFIX}{suffix}"


def validate_relation(relation: str) -> str:
    """Kembalikan relasi bila valid (core/escape/ekstensi); else ValueError.

    Pemanggil (worker) menangkap ValueError → fallback `related_to` +
    catat force-fit, BUKAN gagalkan job.
    """
    if is_core_relation(relation) or is_workspace_extension(relation):
        return relation
    raise ValueError(f"relasi tak dikenal: {relation!r} "
                     f"(core {len(RELATIONS)} + {WORKSPACE_EXT_PREFIX}*)")


def validate_node_type(t: str) -> str:
    if is_core_node_type(t) or is_workspace_extension(t):
        return t
    raise ValueError(f"tipe node tak dikenal: {t!r}")
