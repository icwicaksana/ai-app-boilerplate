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
# Nilai prefix DIBEKUKAN sampai v0.8.2: ia tersimpan di kg_entities.type dan
# kg_edges.relation. Simbol boleh bernama workspace, tapi on-disk tetap
# "tenant:" sampai ada migrasi data + bump versi ontologi.
WORKSPACE_EXT_PREFIX = "tenant:"


def is_core_node_type(t: str) -> bool:
    return t in NODE_TYPES


def is_core_relation(r: str) -> bool:
    return r in RELATIONS


def is_workspace_extension(name: str) -> bool:
    """`workspace:xyz` valid = prefix + nama non-kosong tanpa spasi/paren."""
    if not name.startswith(WORKSPACE_EXT_PREFIX):
        return False
    rest = name[len(WORKSPACE_EXT_PREFIX):]
    return bool(rest) and " " not in rest and "/" not in rest


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
