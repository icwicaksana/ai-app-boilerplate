"""`VectorStorePort` via Postgres: dense HNSW + lexical tsvector (D3, T02/T03).

Aturan keras (D2):
- Instance DIBANGUN sudah terikat workspace (factory). Semua SQL memakai
  `WHERE workspace = $1` eksplisit + `SET LOCAL app.current_workspace` (RLS lapis 3).
- Vektor dikirim sebagai string `'[..]'::vector` (tanpa dep `pgvector-python`;
  kita tak pernah membaca embedding kembali — hanya content/metadata/skor).
- `SET LOCAL` hanya dalam `conn.transaction()` eksplisit (pool autocommit!).
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from app.rag.ports import ChunkIn, CitedChunk, IndexInfo

# Kunci filter metadata yang diizinkan (whitelist anti-injeksi SQL).
FILTER_KEYS = frozenset({"doc_type", "access_level", "source_type", "lang"})


def format_vector(vec: list[float]) -> str:
    """Render list float → literal pgvector. `repr` = roundtrip penuh."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


class PgVectorStore:
    """Adapter pgvector, terikat 1 workspace + 1 pin embedding (D6)."""

    backend_name = "pgvector"

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        workspace: str,
        model: str = "BAAI/bge-m3",
        dim: int = 1024,
        version: str = "v1",
        ef_search: int = 100,
    ) -> None:
        if not workspace:
            raise ValueError("PgVectorStore wajib workspace (factory-bound, D2).")
        self._pool = pool
        self._workspace = workspace
        self._model = model
        self._dim = dim
        self._version = version
        self._ef_search = ef_search

    def _filter_clause(self, filters: dict[str, Any] | None,
                       ) -> tuple[str, list[Any]]:
        if not filters:
            return "", []
        bad = set(filters) - FILTER_KEYS
        if bad:
            raise ValueError(f"kunci filter tak dikenal: {sorted(bad)} "
                             f"(izinkan: {sorted(FILTER_KEYS)})")
        parts, params = [], []
        for key in sorted(filters):
            parts.append(f"c.meta->>'{key}' = %s")
            params.append(str(filters[key]))
        return " AND " + " AND ".join(parts), params

    async def upsert_chunks(self, *, doc_id: str,
                            chunks: list[ChunkIn]) -> int:
        if not chunks:
            return 0
        rows = []
        for c in chunks:
            content = c.get("content", "")
            if not content:
                raise ValueError("chunk tanpa content")
            vec = c.get("vector")
            rows.append((
                self._workspace, doc_id, int(c.get("ord", 0)), content,
                c.get("content_norm"),
                c.get("token_count") or max(1, len(content.split())),
                c.get("page"), c.get("sheet"), c.get("heading"),
                format_vector(vec) if vec is not None else None,
                self._model, self._dim, self._version,
                Json(c.get("metadata") or {}),
            ))
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                cur = conn.cursor()
                await cur.executemany(
                    "INSERT INTO chunks (workspace, document_id, ord, content,"
                    " content_norm,"
                    " token_count, page, sheet, heading, embedding,"
                    " embedding_model, embedding_dim, embedding_version, meta)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s::vector, %s, %s, %s, %s)"
                    " ON CONFLICT (document_id, ord) DO UPDATE SET"
                    " content = EXCLUDED.content,"
                    " content_norm = EXCLUDED.content_norm,"
                    " token_count = EXCLUDED.token_count,"
                    " page = EXCLUDED.page, sheet = EXCLUDED.sheet,"
                    " heading = EXCLUDED.heading, embedding = EXCLUDED.embedding,"
                    " embedding_model = EXCLUDED.embedding_model,"
                    " embedding_dim = EXCLUDED.embedding_dim,"
                    " embedding_version = EXCLUDED.embedding_version,"
                    " meta = EXCLUDED.meta",
                    rows,
                )
        return len(rows)

    async def search_dense(self, vector: list[float], *, k: int,
                           filters: dict[str, Any] | None = None,
                           ) -> list[CitedChunk]:
        clause, fparams = self._filter_clause(filters)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                await conn.execute(f"SET LOCAL hnsw.ef_search = {int(self._ef_search)}")
                cur = await conn.execute(
                    "SELECT c.id, c.document_id, c.ord, c.content, c.page,"
                    " c.sheet, c.heading, c.meta, d.title AS source,"
                    " (c.embedding <=> %s::vector) AS dist"
                    " FROM chunks c JOIN documents d ON d.id = c.document_id"
                    " WHERE c.workspace = %s AND c.embedding IS NOT NULL"
                    + clause +
                    " ORDER BY dist LIMIT %s",
                    [format_vector(vector), self._workspace, *fparams, k],
                )
                rows = await cur.fetchall()
        return [self._to_cited(r, score=-float(r["dist"])) for r in rows]

    async def search_lexical(self, query: str, *, k: int,
                             filters: dict[str, Any] | None = None,
                             raw_query: str | None = None,
                             ) -> list[CitedChunk]:
        # Dual-cabang (v0.7.1): baris BARU cocok via tsv_norm + query
        # ternormalisasi; baris LAMA (content_norm NULL → tsv_norm KOSONG
        # tapi non-null!) cocok via tsv mentah + query mentah. OR = tanpa
        # backfill, nol regresi perilaku warisan. BitmapOr memakai 2 index
        # GIN. plainto_tsquery('') = kueri kosong = tak cocok apa pun.
        raw = raw_query if raw_query is not None else query
        if not (query or "").strip() and not (raw or "").strip():
            return []
        clause, fparams = self._filter_clause(filters)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                cur = await conn.execute(
                    "SELECT c.id, c.document_id, c.ord, c.content, c.page,"
                    " c.sheet, c.heading, c.meta, d.title AS source,"
                    " GREATEST(ts_rank_cd(c.content_tsv_norm,"
                    " plainto_tsquery('simple', %s)),"
                    " ts_rank_cd(c.content_tsv,"
                    " plainto_tsquery('simple', %s))) AS rank"
                    " FROM chunks c JOIN documents d ON d.id = c.document_id"
                    " WHERE c.workspace = %s"
                    " AND (c.content_tsv_norm @@ plainto_tsquery('simple', %s)"
                    " OR c.content_tsv @@ plainto_tsquery('simple', %s))"
                    + clause +
                    " ORDER BY rank DESC LIMIT %s",
                    [query, raw, self._workspace, query, raw, *fparams, k],
                )
                rows = await cur.fetchall()
        return [self._to_cited(r, score=float(r["rank"])) for r in rows]

    async def delete_document(self, *, doc_id: str) -> int:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                cur = await conn.execute(
                    "DELETE FROM chunks WHERE workspace = %s AND document_id = %s",
                    (self._workspace, doc_id),
                )
                return cur.rowcount or 0

    async def index_info(self) -> IndexInfo:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                cur = await conn.execute(
                    "SELECT count(*) AS n FROM chunks WHERE workspace = %s",
                    (self._workspace,))
                n = (await cur.fetchone())["n"]
                cur = await conn.execute(
                    "SELECT embedding_model, embedding_dim, embedding_version"
                    " FROM chunks WHERE workspace = %s LIMIT 1",
                    (self._workspace,))
                row = await cur.fetchone()
        if row is None:
            return {"backend": self.backend_name, "embedding_model": self._model,
                    "embedding_dim": self._dim,
                    "embedding_version": self._version, "chunk_count": 0}
        return {"backend": self.backend_name,
                "embedding_model": row["embedding_model"],
                "embedding_dim": row["embedding_dim"],
                "embedding_version": row["embedding_version"],
                "chunk_count": n}

    @staticmethod
    def _to_cited(row: Any, *, score: float) -> CitedChunk:
        meta = dict(row["meta"] or {})
        chunk_id = meta.pop("_chunk_id", None) or str(row["id"])
        # sheet/heading kolom top-level (sitasi XLSX/MD, K6) — JANGAN hanya
        # di meta. NOTE skala skor: dense=-jarak, lexical=rank (tak
        # sebanding antar-backend; RRF menimpa dengan skor fusi).
        return {"chunk_id": chunk_id, "doc_id": str(row["document_id"]),
                "content": row["content"], "source": row["source"] or "",
                "page": row["page"], "sheet": row["sheet"],
                "heading": row["heading"], "score": score,
                "backend": "pgvector", "metadata": meta}
