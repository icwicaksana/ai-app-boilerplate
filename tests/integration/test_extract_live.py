"""Live ekstraktor L2 (model GLiNER nyata, CPU). Skip bila model absen."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_l2_indonesia():
    import os

    from app.infra.rag.extract.gliner import GlierExtractor

    cache = os.environ.get("HF_HUB_CACHE", ".hf-cache")
    if not os.path.isdir(os.path.join(cache, "models--fastino--gliner2.5-multi-v1")):
        pytest.skip("model GLiNER2.5 belum diunduh")
    ext = GlierExtractor(cache_dir=cache)
    assert (ext.model, ext.version) == ("fastino/gliner2.5-multi-v1", "v1")
    t0 = time.perf_counter()
    out = await ext.extract([
        "PT Sumber Rasa memasok Kopi Arabika ke Cafe Senja.",
        "cuaca hari ini sangat cerah sekali.",
    ])
    el_ms = (time.perf_counter() - t0) * 1000
    rels = {(e["src_norm"], e["relation"], e["dst_norm"])
            for e in out[0]["edges"]}
    assert ("pt sumber rasa", "memasok", "kopi arabika") in rels
    assert out[0]["edges"], "L2 tak menemukan edge sama sekali"
    print(f"\nl2_live_ms={el_ms:.0f}")
