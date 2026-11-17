"""Unit ekstraktor L1/L3/layered (fake, tanpa model/LLM)."""

import pytest

from app.infra.rag.extract.l1_rules import L1RulesExtractor
from app.infra.rag.extract.layered import LayeredExtractor


@pytest.mark.asyncio
async def test_l1_pola_dan_factoid():
    ext = L1RulesExtractor()
    res = (await ext.extract([
        "PT Sumber Rasa memasok Kopi Arabika.",
        "Kopi Tubruk dipasok PT Tiga Rasa.",
        "[Pemasok] nama: PT X | kota: Bandung",
    ]))
    rels = {(e["src_norm"], e["relation"], e["dst_norm"])
            for e in res[0]["edges"]}
    assert ("pt sumber rasa", "memasok", "kopi arabika") in rels
    rels1 = {(e["src_norm"], e["relation"], e["dst_norm"])
             for e in res[1]["edges"]}
    assert ("pt tiga rasa", "memasok", "kopi tubruk") in rels1  # balik benar
    assert len(res[2]["edges"]) == 1  # sel ke-2 → kunci kolom-1
    assert all(e["confidence"] == 1.0 and e["layer"] == "L1"
               for r in res for e in r["edges"])


@pytest.mark.asyncio
async def test_l1_tak_lintas_kalimat_chunk_sebaris():
    """Regresi F5 run 3: chunk 1-baris multi-kalimat → span intra-kalimat."""
    ext = L1RulesExtractor()
    chunk = ("PT Sumber Rasa memasok Kopi Arabika. Kopi Arabika dipakai di "
             "Menu Susu. Gula Aren dipakai di Menu Teh. PT Tiga Rasa "
             "memasok Teh Melati.")
    res = (await ext.extract([chunk]))[0]
    for e in res["edges"]:
        assert "." not in e["src_norm"] and "." not in e["dst_norm"]
        assert len(e["src_norm"]) <= 80 and len(e["dst_norm"]) <= 80
    pairs = {(e["src_norm"], e["relation"], e["dst_norm"])
             for e in res["edges"]}
    assert ("pt tiga rasa", "memasok", "teh melati") in pairs
    assert ("pt sumber rasa", "memasok", "kopi arabika") in pairs
    # Monster run 3 (span multi-kalimat) wajib hilang: tepat 4 edge bersih.
    assert len(pairs) == 4


class _L2:
    model = "l2-fake"
    version = "v9"
    calls = 0

    async def extract(self, texts):
        type(self).calls += 1
        return [{"entities": [], "edges": [{
            "src_norm": "a", "dst_norm": "b", "relation": "terkait_dengan",
            "confidence": 0.4, "layer": "L2", "coref_resolved": True,
            "chunk_ord": 0, "chunk_id": "",
            "chunk_row_id": None}]} for _ in texts]


class _L3:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def extract(self, texts):
        self.calls += len(texts)
        if self.fail:
            from app.core.errors import ExtractorError
            raise ExtractorError("down")
        return [{"entities": [], "edges": [{
            "src_norm": "a", "dst_norm": "b", "relation": "memasok",
            "confidence": 0.7, "layer": "L3", "coref_resolved": True,
            "chunk_ord": 0, "chunk_id": "",
            "chunk_row_id": None}]} for _ in texts]


@pytest.mark.asyncio
async def test_layered_trigger_cap_dan_fallback():
    _L2.calls = 0
    l3 = _L3()
    ext = LayeredExtractor(L1RulesExtractor(), _L2(), l3)
    assert (ext.model, ext.version) == ("l2-fake", "v9")
    texts = ["teks biasa tanpa pola"] * 4 + ["harga 50000 rupiah"] * 4
    out = await ext.extract(texts)
    assert len(out) == 8
    # 4 sensitif + force-fit → trigger; budget min(20, max(1, 10%*8)=1) = 1.
    assert l3.calls == 1
    assert ext.last_stats["l3_calls"] == 1
    # Fallback: L3 mati → tetap hasil L1+L2.
    ext2 = LayeredExtractor(L1RulesExtractor(), _L2(), _L3(fail=True))
    out2 = await ext2.extract(["harga 50000 rupiah"])
    assert out2[0]["edges"] and ext2.last_stats["l3_ok"] == 0


@pytest.mark.asyncio
async def test_layered_skip_l2_baris_tabel():
    _L2.calls = 0
    ext = LayeredExtractor(L1RulesExtractor(), _L2(), None)
    await ext.extract(["[S] a: 1 | b: 2 | c: 3"])
    assert ext.last_stats["skipped_l2"] == 1
    assert _L2.calls == 0


def test_l2_sentence_filter_tolak_lintas_kalimat():
    from app.infra.rag.extract.gliner import GlierExtractor

    text = "PT Sumber Rasa memasok Kopi Arabika.\nPT Tiga Rasa memasok Teh."
    ent = {"entities": {
        "organisasi": [{"text": "PT Sumber Rasa", "confidence": 0.9},
                       {"text": "PT Tiga Rasa", "confidence": 0.9}],
        "produk": [{"text": "Kopi Arabika", "confidence": 0.9},
                   {"text": "Teh", "confidence": 0.9}]}}
    rel = {"relation_extraction": {
        "memasok": [
            {"head": {"text": "PT Sumber Rasa"}, "tail": {"text": "Kopi Arabika"},
             "confidence": 0.58},
            {"head": {"text": "PT Sumber Rasa"}, "tail": {"text": "Teh"},
             "confidence": 0.58},
        ]}}
    out = GlierExtractor._to_extraction(ent, rel, text)
    got = {(e["src_norm"], e["dst_norm"]) for e in out["edges"]}
    assert ("pt sumber rasa", "kopi arabika") in got
    assert ("pt sumber rasa", "teh") not in got  # lintas kalimat → tolak


@pytest.mark.asyncio
async def test_l3_cache_breaker_kuota():
    from app.infra.rag.extract.llm_l3 import LlmExtractor, _L3Result

    class _Struct:
        def __init__(self, llm):
            self._llm = llm

        async def ainvoke(self, prompt):
            self._llm.calls += 1
            if self._llm.fail:
                raise RuntimeError("429 rate limited")
            return _L3Result.model_validate(
                {"entities": [{"name": "PT X", "type": "organisasi"}],
                 "relations": []})

    class _LLM:
        def __init__(self, fail=False):
            self.fail = fail
            self.calls = 0

        def with_structured_output(self, schema):
            return _Struct(self)

    llm = _LLM()
    ext = LlmExtractor(llm, model="m", daily_quota=10)
    r1 = (await ext.extract(["PT X memasok kopi"]))[0]
    assert r1["entities"][0]["name"] == "PT X"
    await ext.extract(["PT X memasok kopi"])
    assert llm.calls == 1  # cache hit
    bad = LlmExtractor(_LLM(fail=True), model="m", breaker_minutes=15)
    with pytest.raises(Exception, match="L3 gagal"):
        await bad.extract(["teks sensitif 123"])
    with pytest.raises(Exception, match="breaker"):
        await bad.extract(["teks lain 456"])  # 429 = trip segera
    q = LlmExtractor(_LLM(), model="m", daily_quota=1)
    await q.extract(["satu"])
    with pytest.raises(Exception, match="kuota"):
        await q.extract(["dua"])
