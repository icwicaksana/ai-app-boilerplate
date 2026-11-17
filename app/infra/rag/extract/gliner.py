"""L2 GLiNER zero-shot (F4): joint entity+relation CPU, skema ontologi core.

- Model: `fastino/gliner2.5-multi-v1` (287M, Apache-2.0) via `gliner2[local]`.
- Load MALAS (pertama `extract`): worker tanpa graph tak membayar ±600MB.
- Label = tipe node + relasi core `kg_onto/v1` (constrained decoding alami).
- Inferensi SYNC di thread (`asyncio.to_thread`): tanpa `asyncio` di app/rag
  (ini adapter infra — boleh), tanpa block event loop.
- Gagal load/inferensi = raise `ExtractorError` (D6: tanpa fallback diam).
"""

from __future__ import annotations

import asyncio

from app.rag.normalize import normalize_entity
from app.rag.ontology import NODE_TYPES, RELATIONS

_EXTRACTION_TIMEOUT_S = 120


def _resolve_snapshot(model_id: str, cache_dir: str) -> str | None:
    """Snapshot lokal terbaru di cache repo (layout hub standar)."""
    import os

    slug = "models--" + model_id.replace("/", "--")
    snap_dir = os.path.join(cache_dir, slug, "snapshots")
    if not os.path.isdir(snap_dir):
        return None
    cands = [os.path.join(snap_dir, d) for d in os.listdir(snap_dir)]
    cands = [d for d in cands if os.path.isdir(d)]
    if not cands:
        return None
    cands.sort(key=os.path.getmtime, reverse=True)
    return cands[0]


class GlierExtractor:
    """`EntityExtractor` L2 (GLiNER2.5 joint IE)."""

    def __init__(self, *, model: str = "fastino/gliner2.5-multi-v1",
                 version: str = "v1", cache_dir: str = ".hf-cache",
                 threshold: float = 0.3, batch_size: int = 8,
                 timeout_s: int = _EXTRACTION_TIMEOUT_S) -> None:
        self._model_id = model
        self._version = version
        self._cache_dir = cache_dir
        self._threshold = threshold
        self._batch_size = batch_size
        self._timeout_s = timeout_s
        self._model = None

    @property
    def model(self) -> str:
        return self._model_id

    @property
    def version(self) -> str:
        return self._version

    def _load(self):
        if self._model is None:
            from gliner2 import AutoExtractor

            local = _resolve_snapshot(self._model_id, self._cache_dir)
            if local is None:
                from app.core.errors import ExtractorError

                raise ExtractorError(
                    f"Model {self._model_id} tak ada di {self._cache_dir}. "
                    "Unduh dulu (lihat runbook v0.8).")
            try:
                # Path lokal eksplisit: tak pernah sentuh ~/.cache (masalah
                # lock/permission Windows, pelajaran tokenizer v0.7).
                self._model = AutoExtractor.from_pretrained(
                    local, map_location="cpu", local_files_only=True)
            except Exception as exc:
                from app.core.errors import ExtractorError

                raise ExtractorError(
                    f"GLiNER gagal dimuat ({self._model_id}): {exc}") from exc
        return self._model

    async def extract(self, texts: list[str]) -> list[dict]:
        from app.core.errors import ExtractorError

        model = self._load()
        labels_e = sorted(NODE_TYPES)
        labels_r = sorted(RELATIONS)
        try:
            ent_all, rel_all = await asyncio.wait_for(
                asyncio.to_thread(self._run_batch, model, list(texts),
                                  labels_e, labels_r),
                timeout=self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise ExtractorError(
                f"GLiNER timeout {self._timeout_s}s ({len(texts)} teks).") from exc
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError(f"GLiNER gagal: {exc}") from exc
        return [self._to_extraction(e, r, t)
                for e, r, t in zip(ent_all, rel_all, texts)]

    def _run_batch(self, model, texts, labels_e, labels_r):
        ent = model.batch_extract_entities(
            texts, labels_e, batch_size=self._batch_size,
            threshold=self._threshold, include_confidence=True)
        rel = model.batch_extract_relations(
            texts, labels_r, batch_size=self._batch_size,
            threshold=self._threshold, include_confidence=True)
        return ent, rel

    @staticmethod
    def _to_extraction(ent_res: dict, rel_res: dict, text: str = "") -> dict:
        """Petakan format BoundaryExtractor (terverifikasi probe F4):

        - entities: {"entities": {label: [{text, confidence}]}}
        - relations: {"relation_extraction":
          {label: [{head: {text}, tail: {text}, confidence}]}}

        Filter ko-okurensi kalimat (pelajaran F5 run 2): edge L2 DITOLAK
        kecuali kedua endpoint muncul dalam 1 kalimat yang sama. Model
        zero-shot menghalusinasi pasangan lintas-kalimat pada confidence
        TINGGI (0.58–0.62, tak terpisah threshold) — satu-satunya pemisah
        jujur adalah ko-okurensi. Relasi lintas-kalimat sejati (coref) =
        tier-M (butuh coref resolver).
        """
        entities: dict[str, dict] = {}
        grouped = ent_res.get("entities") or {}
        items = []
        if isinstance(grouped, dict):
            for label, spans in grouped.items():
                for s in spans or []:
                    items.append((label, s))
        elif isinstance(grouped, list):
            for s in grouped:
                items.append((s.get("label", "lain"), s))
        for label, s in items:
            if not isinstance(s, dict):
                continue
            name = (s.get("text") or "").strip()
            if not name:
                continue
            norm = normalize_entity(name)
            if not norm or norm in entities:
                continue
            entities[norm] = {
                "name": name, "norm": norm, "type": label or "lain",
                "confidence": float(s.get("confidence", 0.5)),
                "chunk_ord": 0, "chunk_id": "",
            }
        edges: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        rgrouped = rel_res.get("relation_extraction") or {}
        ritems = []
        if isinstance(rgrouped, dict):
            for label, rels in rgrouped.items():
                for r in rels or []:
                    ritems.append((label, r))
        for label, r in ritems:
            if not isinstance(r, dict):
                continue
            head = r.get("head") or {}
            tail = r.get("tail") or {}
            s = normalize_entity(head.get("text", "") if isinstance(
                head, dict) else "")
            o = normalize_entity(tail.get("text", "") if isinstance(
                tail, dict) else "")
            if not s or not o or s == o or (s, o, label) in seen:
                continue
            seen.add((s, o, label))
            conf = float(r.get("confidence", 0.5))
            edges.append({
                "src_norm": s, "dst_norm": o, "relation": label,
                "confidence": conf,
                "layer": "L2", "coref_resolved": True,
                # v1.0 G1: TANPA access_level (SoR = chunk/dokumen).
                "chunk_ord": 0, "chunk_id": "",
                "chunk_row_id": None,
            })
        # Entity dari relasi yang tak ada di NER → daftarkan (tanpa orphan).
        for e in edges:
            for norm in (e["src_norm"], e["dst_norm"]):
                if norm not in entities:
                    entities[norm] = {
                        "name": norm, "norm": norm, "type": "lain",
                        "confidence": e["confidence"], "chunk_ord": 0,
                        "chunk_id": "",
                    }
        if text:
            edges = GlierExtractor._sentence_filter(edges, entities, text)
        return {"entities": list(entities.values()), "edges": edges}

    @staticmethod
    def _sentence_filter(edges: list[dict], entities: dict,
                         text: str) -> list[dict]:
        """Tolak edge yang endpoint-nya tak se-kalimat (anti-halusinasi)."""
        import re

        sents = [normalize_entity(s) for s in
                 re.split(r"[.!?\n]+", text or "")]
        sents = [s for s in sents if s]
        names = {norm: normalize_entity(e.get("name") or norm)
                 for norm, e in entities.items()}
        kept: list[dict] = []
        for e in edges:
            s = names.get(e["src_norm"], e["src_norm"])
            o = names.get(e["dst_norm"], e["dst_norm"])
            if any(s in sent and o in sent for sent in sents):
                kept.append(e)
        return kept
