"""L3 LLM budgeted (F4): ekstraksi via gateway untuk chunk RAGU saja.

Dipanggil orchestrator (`layered`) HANYA bila trigger terpenuhi
(conf_L2 < 0.5 DAN chunk sensitif). Pengaman biaya berlapis:
cache-hash 7 hari → breaker → kuota harian → cap per-dok (orkestrator).

Penyederhanaan F4 (tercatat, konservatif): kuota = per-PROSES per-hari
(bukan per-workspace — protocol `extract` tanpa workspace; split per-workspace =
tier-M). Nilai kuota dipakai apa adanya → bound global lebih ketat.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from pydantic import BaseModel, Field


class _L3Entity(BaseModel):
    name: str = ""
    type: str = "lain"


class _L3Relation(BaseModel):
    src: str = ""
    dst: str = ""
    relation: str = "terkait_dengan"


class _L3Result(BaseModel):
    entities: list[_L3Entity] = Field(default_factory=list)
    relations: list[_L3Relation] = Field(default_factory=list)


_PROMPT = """Ekstrak entity dan relasi dari TEKS berikut (Bahasa Indonesia).
Tipe entity: orang, organisasi, produk, lokasi, dokumen, tanggal, harga, kebijakan, acara, lain.
Relasi: memasok, dipakai_di, bagian_dari, berlokasi_di, bekerja_sebagai, memiliki, menerbitkan, berlaku_sejak, berlaku_sampai, mensyaratkan, melarang, menggantikan, terkait_dengan, menyebut.
Hanya fakta EKSPLISIT di teks. Maks 12 entity, 12 relasi. Nama entity = span persis dari teks.

TEKS:
{text}
"""


class LlmExtractor:
    """`EntityExtractor` L3 via chat model gateway (structured output)."""

    def __init__(self, llm, *, model: str, version: str = "v1",
                 max_tokens_in: int = 1500, daily_quota: int = 500,
                 breaker_minutes: int = 15, cache=None,
                 cache_ttl_days: int = 7) -> None:
        self._llm = llm
        self._model = model
        self._version = version
        self._max_in = max_tokens_in
        self._quota = daily_quota
        self._breaker_min = breaker_minutes
        self._cache = cache
        self._ttl = cache_ttl_days * 86400
        self._mem_cache: dict[str, Any] = {}
        self._used_today = 0
        self._day = time.strftime("%Y-%m-%d")
        self._fails = 0
        self._open_until = 0.0

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    def _key(self, text: str) -> str:
        raw = f"l3:{self._model}:{self._version}:{_PROMPT}:{text}"
        return "kg:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def _cache_get(self, key: str):
        if key in self._mem_cache:
            return self._mem_cache[key]
        if self._cache is None:
            return None
        try:
            raw = await self._cache.get_json(key)
        except Exception:
            return None
        return raw

    async def _cache_set(self, key: str, value: Any) -> None:
        if len(self._mem_cache) >= 1000:
            self._mem_cache.pop(next(iter(self._mem_cache)))
        self._mem_cache[key] = value
        if self._cache is not None:
            try:
                await self._cache.set_json(key, value, ttl=self._ttl)
            except Exception:
                pass

    async def _quota_take(self) -> bool:
        """Ambil 1 slot kuota harian (v1.0 P1: bersama antar-proses).

        Valkey INCR atomik (kunci harian) bila cache ada; fallback counter
        lokal bila cache None/mati (kuota jadi per-proses — didokumentasikan,
        bukan senyap: overshoot terbatas jumlah worker).
        """
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._used_today = today, 0
        if self._cache is not None:
            try:
                n, _ = await self._cache.incr_window(
                    f"kg:l3quota:{today}", 86400)
                return n <= self._quota
            except Exception:
                pass  # cache mati → fallback lokal di bawah
        self._used_today += 1
        return self._used_today <= self._quota

    def _quota_ok(self) -> bool:
        """Cek cepat TANPA ambil slot (kompatibel pemanggil lama/test)."""
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._used_today = today, 0
        return self._used_today < self._quota

    async def extract(self, texts: list[str]) -> list[dict]:
        out: list[dict] = []
        for text in texts:
            out.append(await self._one(text or ""))
        return out

    async def _one(self, text: str) -> dict:
        from app.core.errors import ExtractorError

        empty: dict = {"entities": [], "edges": []}
        if not text.strip():
            return empty
        key = self._key(text)
        cached = await self._cache_get(key)
        if cached is not None:
            return cached
        if time.time() < self._open_until:
            raise ExtractorError("L3 breaker terbuka (cooldown).")
        if not await self._quota_take():
            raise ExtractorError("L3 kuota harian habis.")
        clipped = text[: self._max_in * 4]  # ≈ token→char
        try:
            struct = self._llm.with_structured_output(_L3Result)
            res: _L3Result = await struct.ainvoke(
                _PROMPT.format(text=clipped))
        except Exception as exc:
            self._fails += 1
            msg = f"{type(exc).__name__}: {exc}"
            # 402/429 = trip SEGERA (uang/rate); else 3x beruntun.
            if "402" in msg or "429" in msg or self._fails >= 3:
                self._open_until = time.time() + self._breaker_min * 60
            raise ExtractorError(f"L3 gagal: {msg[:200]}") from exc
        self._fails = 0
        entities = [{
            "name": e.name.strip(), "norm": "", "type": e.type.strip() or "lain",
            "confidence": 0.7, "chunk_ord": 0, "chunk_id": "",
        } for e in res.entities if e.name.strip()][:12]
        edges = [{
            "src_norm": r.src.strip(), "dst_norm": r.dst.strip(),
            "relation": r.relation.strip() or "terkait_dengan",
            "confidence": 0.7, "layer": "L3", "coref_resolved": True,
            # v1.0 G1: TANPA access_level (SoR = chunk/dokumen).
            "chunk_ord": 0, "chunk_id": "",
            "chunk_row_id": None,
        } for r in res.relations if r.src.strip() and r.dst.strip()][:12]
        # norm dihitung worker (sumber tunggal); src/dst dinormalisasi di sini
        # karena worker hanya normalize name (kontrak worker: norm dari name).
        from app.rag.normalize import normalize_entity

        for e in edges:
            e["src_norm"] = normalize_entity(e["src_norm"])
            e["dst_norm"] = normalize_entity(e["dst_norm"])
        result = {"entities": entities, "edges": edges}
        await self._cache_set(key, result)
        return result
