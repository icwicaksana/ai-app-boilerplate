"""Orkestrator ekstraksi berlapis L1→L2→L3 (F4).

Aturan (DESIGN §4/D5 + QA efisiensi):
- L1 (aturan) + L2 (GLiNER) selalu jalan per teks; union (prioritas L1).
- L2 di-SKIP bila L1 cukup (baris tabel + ≥2 edge) — hemat 30–50%.
- L3 (LLM) hanya bila: L2 menyerah (0 edge ATAU semua edge < 0.5)
  DAN chunk sensitif (angka/kebijakan/komparasi/force-fit).
  Gagal L3 → fallback L2 + lanjut (tak pernah gagalkan batch). Gagal L2 → propagate (D6: tanpa fallback
  diam-diam — worker retry/fail eksplisit).
- Cap L3 per panggilan batch: min(20, maks(1, 10% batch)) + kuota harian
  (backstop). Untuk dok ≤160 chunk ini menghormati cap 20/dok + 10%.

`model`/`version` = pin L2 (primer, D6). Observabilitas: `last_stats`.
"""

from __future__ import annotations

import re

_SENSITIVE_RE = re.compile(
    r"\d|syarat|ketentuan|kebijakan|berlaku|dilarang|wajib|dibanding|"
    r"dibandingkan|lebih (murah|mahal|besar|kecil|cepat|lambat)",
    re.IGNORECASE)


class LayeredExtractor:
    """`EntityExtractor` gabungan L1+L2[+L3]."""

    def __init__(self, l1, l2, l3=None, *, l3_max_calls: int = 20,
                 l3_frac: float = 0.10) -> None:
        self._l1 = l1
        self._l2 = l2
        self._l3 = l3
        self._l3_max = l3_max_calls
        self._l3_frac = l3_frac
        self.last_stats: dict = {}

    @property
    def model(self) -> str:
        return self._l2.model

    @property
    def version(self) -> str:
        return self._l2.version

    def set_cache(self, cache) -> None:
        """Suntik cache Valkey worker ke L3 (TTL hari; None = memori saja)."""
        if self._l3 is not None and getattr(self._l3, "_cache", None) is None:
            self._l3._cache = cache

    @staticmethod
    def _sensitive(text: str, l2res: dict) -> bool:
        if _SENSITIVE_RE.search(text or ""):
            return True
        # Force-fit = L2 menyerah ke relasi generik.
        return any(e.get("relation") in ("terkait_dengan", "related_to")
                   for e in (l2res.get("edges") or []))

    @staticmethod
    def _needs_l3(text: str, l2res: dict) -> bool:
        """Trigger L3 (hasil spike F4: 2/12 sampel, bukan 6/12 min-conf).

        L2 "menyerah" = 0 edge ATAU semua edge < 0.5, DAN chunk sensitif.
        (min-conf mentah terlalu agresif: 1 edge lemah memicu 1 teks.)
        """
        edges = l2res.get("edges") or []
        mx = max((e.get("confidence", 0.0) for e in edges), default=0.0)
        return (not edges or mx < 0.5) and LayeredExtractor._sensitive(
            text, l2res)

    @staticmethod
    def _min_conf(res: dict) -> float:
        confs = [e.get("confidence", 1.0)
                 for e in (res.get("edges") or [])]
        confs += [e.get("confidence", 1.0)
                  for e in (res.get("entities") or [])]
        return min(confs) if confs else 1.0

    @staticmethod
    def _merge(a: dict, b: dict) -> dict:
        """Union; `a` (L1/L2) menang atas `b` (lapis lebih lemah)."""
        ents = {(e.get("norm") or e.get("name")): e
                for e in (b.get("entities") or [])}
        ents.update({(e.get("norm") or e.get("name")): e
                     for e in (a.get("entities") or [])})
        edges = {(e.get("src_norm"), e.get("dst_norm"), e.get("relation")): e
                 for e in (b.get("edges") or [])}
        edges.update({(e.get("src_norm"), e.get("dst_norm"), e.get("relation")): e
                      for e in (a.get("edges") or [])})
        return {"entities": list(ents.values()), "edges": list(edges.values())}

    async def extract(self, texts: list[str]) -> list[dict]:
        l1_all = await self._l1.extract(texts)
        # Skip-L2 bila L1 cukup (baris tabel + ≥2 edge deterministik).
        need_l2 = [
            not ("|" in (t or "") and len(r.get("edges") or []) >= 2)
            for t, r in zip(texts, l1_all)
        ]
        l2_all: list[dict | None] = [None] * len(texts)
        idx = [i for i, need in enumerate(need_l2) if need]
        if idx:
            got = await self._l2.extract([texts[i] for i in idx])
            for i, r in zip(idx, got):
                l2_all[i] = r
        merged = [self._merge(l1, l2 or {"entities": [], "edges": []})
                  for l1, l2 in zip(l1_all, l2_all)]
        stats = {"n": len(texts), "skipped_l2": len(texts) - len(idx),
                 "l3_calls": 0, "l3_ok": 0}
        if self._l3 is not None and texts:
            budget = min(self._l3_max,
                         max(1, int(len(texts) * self._l3_frac)))
            for i, (t, l2r) in enumerate(zip(texts, l2_all)):
                if stats["l3_calls"] >= budget:
                    break
                if l2r is None or not self._needs_l3(t, l2r):
                    continue
                stats["l3_calls"] += 1
                try:
                    l3r = (await self._l3.extract([t]))[0]
                except Exception:
                    continue  # fallback L2 + lanjut (tak pernah gagal batch)
                merged[i] = self._merge(merged[i], l3r)
                stats["l3_ok"] += 1
        self.last_stats = stats
        return merged
