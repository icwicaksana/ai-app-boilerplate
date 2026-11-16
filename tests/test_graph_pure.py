"""Unit ontologi + verbalize (murni, tanpa I/O)."""

import pytest

from app.rag.ontology import (
    ESCAPE_RELATION,
    NODE_TYPES,
    ONTO_VERSION,
    RELATIONS,
    is_workspace_extension,
    validate_node_type,
    validate_relation,
)
from app.rag.verbalize import verbalize_path


def test_core_beku_10_15():
    assert ONTO_VERSION == "kg_onto/v1"
    assert len(NODE_TYPES) == 10 and len(RELATIONS) == 15
    assert ESCAPE_RELATION in RELATIONS


def test_validasi_terima_core_dan_ekstensi():
    assert validate_relation("memasok") == "memasok"
    assert validate_relation("tenant:nomor_batch") == "tenant:nomor_batch"
    assert validate_node_type("produk") == "produk"
    with pytest.raises(ValueError):
        validate_relation("memakan")
    with pytest.raises(ValueError):
        validate_relation("tenant:")  # nama kosong
    with pytest.raises(ValueError):
        validate_node_type("alien")


def test_is_workspace_extension():
    assert is_workspace_extension("tenant:kode_hs")
    assert not is_workspace_extension("memasok")
    assert not is_workspace_extension("tenant:ada spasi")


def test_verbalize_dua_hop_tanpa_transitivitas():
    out = verbalize_path([
        ("Kopi Arabika", "memasok", "PT Sumber Rasa"),  # arah contoh bebas
        ("PT Sumber Rasa", "berlokasi_di", "Bandung"),
    ])
    assert out == ("Kopi Arabika memasok PT Sumber Rasa. "
                   "PT Sumber Rasa berlokasi di Bandung.")


def test_verbalize_fallback_dan_sanitasi():
    assert verbalize_path([("A", "tenant:kode_hs", "B")]) == "A kode_hs B."
    out = verbalize_path([("A<<<", "melarang", "B>>>")])
    assert "<<<" not in out and ">>>" not in out
    assert verbalize_path([("", "memasok", "B")]) == ""
    long3 = [("A", "menyebut", "B")] * 3
    assert verbalize_path(long3).count(".") == 2  # potong jadi 2
