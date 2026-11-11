"""`VectorStorePort` via Qdrant (D4, T08/T09, M3).

Keputusan implementasi (melengkapi desain; lihat addendum A1.5):
- Payload menyimpan `content` PENUH (bukan prefix): port mensyaratkan
  retrieval mandiri per backend. Duplikasi ±7KB/chunk diterima di skala SMB.
- Lexical = boolean `MatchText` + urut deterministik client-side
  (document_id, ord), skor konstanta 1.0. Qdrant tanpa fastembed tak memberi
  ranking BM25 server-side — divergensi vs tsvector PG dicatat di golden test (R7).
- Filter tenant SELALU di-inject wrapper (lapis 1); query tanpa tenant mustahil
  karena tenant terkunci di konstruktor (D2).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.rag.ports import ChunkIn, CitedChunk, IndexInfo

FILTER_KEYS = frozenset({"doc_type", "access_level", "source_type", "lang"})


def collection_name(dim: int, model: str) -> str:
    """Nama koleksi mengandung dim+model (D4): ganti model tak menimpa index lama."""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return f"chunks_{int(dim)}_{slug}"


def point_id(tenant: str, doc_id: str, ord_: int) -> str:
    """ID point deterministik (upsert idempoten + anti tabrakan lintas tenant)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant}:{doc_id}:{ord_}"))


class QdrantStore:
    """Adapter Qdrant, terikat 1 tenant + 1 pin embedding (D6)."""

    backend_name = "qdrant"

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        *,
        tenant: str,
        model: str = "BAAI/bge-m3",
        dim: int = 1024,
        version: str = "v1",
    ) -> None:
        if not tenant:
            raise ValueError("QdrantStore wajib tenant (factory-bound, D2).")
        self._client = client
        self._collection = collection
        self._tenant = tenant
        self._model = model
        self._dim = dim
        self._version = version

    # -- setup (dipanggil factory/lifespan, BUKAN per request) --

    async def ensure_collection(self) -> None:
        """Buat koleksi + index bila belum ada. Idempotent."""
        from qdrant_client.http.exceptions import UnexpectedResponse

        try:
            await self._client.get_collection(self._collection)
            return
        except UnexpectedResponse as exc:
            if exc.status_code != 404:
                raise  # 401/5xx = masalah nyata, JANGAN telan (P1)
        await self._client.create_collection(
            self._collection,
            vectors_config=models.VectorParams(
                size=self._dim, distance=models.Distance.COSINE,
                on_disk=True),
            hnsw_config=models.HnswConfigDiff(m=16, ef_construct=100),
            quantization_config=models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8, quantile=0.99,
                    always_ram=True)),
            on_disk_payload=True,
        )
        # Filter tenant + doc top-level; filter metadata nested meta.* (P2-14).
        for field in ("tenant", "document_id", "meta.source_type",
                      "meta.lang"):
            await self._client.create_payload_index(
                self._collection, field,
                field_schema=models.PayloadSchemaType.KEYWORD)
        await self._client.create_payload_index(
            self._collection, "content",
            field_schema=models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.WORD,
                lowercase=True))

    # -- VectorStorePort --

    def _base_filter(self, filters: dict[str, Any] | None) -> models.Filter:
        bad = set(filters or ()) - FILTER_KEYS
        if bad:
            raise ValueError(f"kunci filter tak dikenal: {sorted(bad)} "
                             f"(izinkan: {sorted(FILTER_KEYS)})")
        must: list[Any] = [
            models.FieldCondition(key="tenant",
                                  match=models.MatchValue(value=self._tenant))]
        for key in sorted(filters or ()):
            must.append(models.FieldCondition(
                key=f"meta.{key}",
                match=models.MatchValue(value=str(filters[key]))))
        return models.Filter(must=must)

    async def upsert_chunks(self, *, doc_id: str,
                            chunks: list[ChunkIn]) -> int:
        if not chunks:
            return 0
        points = []
        for c in chunks:
            content = c.get("content", "")
            if not content:
                raise ValueError("chunk tanpa content")
            vec = c.get("vector")
            if vec is None:
                raise ValueError("chunk tanpa vector (Qdrant wajib dense)")
            ord_ = int(c.get("ord", 0))
            meta = dict(c.get("metadata") or {})
            points.append(models.PointStruct(
                id=point_id(self._tenant, doc_id, ord_),
                vector=[float(x) for x in vec],
                payload={
                    "tenant": self._tenant, "document_id": doc_id,
                    "ord": ord_, "content": content,
                    "page": c.get("page"), "sheet": c.get("sheet"),
                    "heading": c.get("heading"),
                    "token_count": c.get("token_count") or 1,
                    "source": meta.pop("_source", ""),
                    "meta": meta,
                    "embedding_model": self._model,
                    "embedding_dim": self._dim,
                    "embedding_version": self._version,
                }))
        await self._client.upsert(self._collection, points, wait=True)
        return len(points)

    async def search_dense(self, vector: list[float], *, k: int,
                           filters: dict[str, Any] | None = None,
                           ) -> list[CitedChunk]:
        res = await self._client.query_points(
            self._collection, query=[float(x) for x in vector],
            query_filter=self._base_filter(filters), limit=k,
            with_payload=True)
        return [self._to_cited(p) for p in res.points]

    async def search_lexical(self, query: str, *, k: int,
                             filters: dict[str, Any] | None = None,
                             ) -> list[CitedChunk]:
        filt = self._base_filter(filters)
        filt.must.append(models.FieldCondition(
            key="content", match=models.MatchText(text=query)))
        points, _ = await self._client.scroll(
            self._collection, scroll_filter=filt, limit=k * 5,
            with_payload=True, with_vectors=False)
        # Urut deterministik client-side (skor boolean tak bermakna ranking).
        points.sort(key=lambda p: (str(p.payload.get("document_id", "")),
                                   int(p.payload.get("ord", 0))))
        return [self._to_cited(p, score=1.0) for p in points[:k]]

    async def delete_document(self, *, doc_id: str) -> int:
        filt = models.Filter(must=[
            models.FieldCondition(key="tenant", match=models.MatchValue(
                value=self._tenant)),
            models.FieldCondition(key="document_id", match=models.MatchValue(
                value=doc_id))])
        before = await self._client.count(
            self._collection, count_filter=filt, exact=True)
        await self._client.delete(
            self._collection,
            points_selector=models.FilterSelector(filter=filt), wait=True)
        return before.count

    async def index_info(self) -> IndexInfo:
        filt = models.Filter(must=[models.FieldCondition(
            key="tenant", match=models.MatchValue(value=self._tenant))])
        n = (await self._client.count(
            self._collection, count_filter=filt, exact=True)).count
        if n == 0:
            return {"backend": self.backend_name, "embedding_model": self._model,
                    "embedding_dim": self._dim,
                    "embedding_version": self._version, "chunk_count": 0}
        points, _ = await self._client.scroll(
            self._collection, scroll_filter=filt, limit=1, with_payload=True,
            with_vectors=False)
        p = points[0].payload or {}
        return {"backend": self.backend_name,
                "embedding_model": str(p.get("embedding_model", self._model)),
                "embedding_dim": int(p.get("embedding_dim", self._dim)),
                "embedding_version": str(p.get("embedding_version",
                                               self._version)),
                "chunk_count": n}

    def _to_cited(self, point: Any, *, score: float | None = None) -> CitedChunk:
        p = point.payload or {}
        meta = dict(p.get("meta") or {})
        chunk_id = meta.pop("_chunk_id", None) or str(point.id)
        s = point.score if score is None else score
        return {"chunk_id": chunk_id, "doc_id": str(p.get("document_id", "")),
                "content": str(p.get("content", "")),
                "source": str(p.get("source", "")),
                "page": p.get("page"), "sheet": p.get("sheet"),
                "heading": p.get("heading"), "score": float(s or 0.0),
                "backend": "qdrant", "metadata": meta}
