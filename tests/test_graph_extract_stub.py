"""Unit stub extractor (deterministik, tanpa I/O)."""

import pytest

from app.infra.rag.extract.stub import StubExtractor


@pytest.mark.asyncio
async def test_stub_temukan_pola_memasok_dan_lokasi():
    ext = StubExtractor()
    assert (ext.model, ext.version) == ("stub-deterministik", "f2")
    res = (await ext.extract([
        "PT Sumber Rasa memasok Kopi Arabika.",
        "PT Sumber Rasa berlokasi di Bandung.",
        "teks biasa tanpa pola relasi",
    ]))
    assert len(res) == 3
    rels = {(e["src_norm"], e["relation"], e["dst_norm"])
            for e in res[0]["edges"]}
    assert ("pt sumber rasa", "memasok", "kopi arabika") in rels
    assert any(e["relation"] == "berlokasi_di" for e in res[1]["edges"])
    assert res[2]["edges"] == []
    assert all(e["layer"] == "L1" for e in res[0]["edges"])
    # Entity kapital terdaftar walau tanpa relasi.
    assert any(e["name"] == "Bandung" for e in res[1]["entities"])


@pytest.mark.asyncio
async def test_stub_tahan_input_kosong():
    ext = StubExtractor()
    assert await ext.extract([]) == []
    res = (await ext.extract([""]))[0]
    assert res == {"entities": [], "edges": []}
