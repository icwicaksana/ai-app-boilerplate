"""`GraphStore` PG-native (v0.8 D1/D3): 2 tabel + frontier BFS bounded.

Aturan keras (meniru `pgvector.py` + DESIGN §2/§4):
- Instance DIBANGUN sudah terikat workspace (factory). Semua SQL memakai
  `WHERE workspace = %s` eksplisit + `SET LOCAL app.current_workspace`.
- `SET LOCAL` hanya dalam `conn.transaction()` eksplisit (pool autocommit!).
- Traversal = BFS iteratif bounded di Python, BUKAN recursive CTE
  (pelajaran LightRAG #3103: RCTE timeout di hub).
- Filter v0.8 = `access_level` SAJA (kolom denormalisasi, D13 tanpa JOIN).
  Kunci lain → ValueError eksplisit (perluasan = perubahan kontrak).
- Timeout/error → raise `TimeoutError`/typed → pipeline ubah jadi
  `graph_unavailable`. TIDAK PERNAH 500 diam-diam (D11).
"""

from __future__ import annotations

import math
from typing import Any

from psycopg import errors as pg_errors
from psycopg_pool import AsyncConnectionPool

from app.rag.normalize import normalize_entity
from app.rag.ontology import (
    ONTO_VERSION,
    validate_node_type,
    validate_relation,
)
from app.rag.ports import (
    GraphInfo,
    KgEdgeIn,
    KgEntityIn,
    KgHit,
)
from app.rag.verbalize import VERBALIZE_VERSION, verbalize_path

# Filter metadata v0.8 graph: access_level saja (kolom edge, tanpa JOIN).
GRAPH_FILTER_KEYS = frozenset({"access_level"})

_MAX_SEEDS = 32
_GLOBAL_EDGE_CAP = 200  # cap path global (bukan per-arah; lihat _level)
_MAX_NODES = 50
_HOP2_DISCOUNT = 0.7
_DEFAULT_CONF = 0.5
_INSERT_CHUNK_ROWS = 500  # multi-row VALUES di-chunk (limit 65535 param PG)


class PgGraphStore:
    """Adapter graph PG-native, terikat 1 workspace (D2)."""

    backend_name = "pggraph"

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        workspace: str,
        model: str = "fastino/gliner2.5-multi-v1",
        version: str = "v1",
        onto_version: str = ONTO_VERSION,
        timeout_ms: int = 800,
    ) -> None:
        if not workspace:
            raise ValueError("PgGraphStore wajib workspace (factory-bound, D2).")
        self._pool = pool
        self._workspace = workspace
        self._model = model
        self._version = version
        self._onto = onto_version
        self._timeout_ms = timeout_ms

    # -- tulis/hapus (worker + controller) --

    async def upsert_subgraph(
        self, *, doc_id: str,
        entities: list[KgEntityIn], edges: list[KgEdgeIn],
    ) -> tuple[int, int]:
        for e in entities:
            validate_node_type(e.get("type", ""))
            if not (e.get("norm") or "").strip():
                raise ValueError("entity tanpa norm")
        for e in edges:
            validate_relation(e.get("relation", ""))
            if not (e.get("src_norm") or "").strip() or not (
                    e.get("dst_norm") or "").strip():
                raise ValueError("edge tanpa src/dst norm")
            if e.get("src_norm") == e.get("dst_norm"):
                raise ValueError("edge self-loop ditolak")
            if e.get("layer") not in (None, "L1", "L2", "L3"):
                raise ValueError(f"layer edge tak dikenal: {e.get('layer')!r}")
            conf = e.get("confidence")
            if conf is not None and not 0.0 <= float(conf) <= 1.0:
                raise ValueError(f"confidence edge di luar 0..1: {conf!r}")
            if not (e.get("chunk_id") or "").strip():
                # G1: provenance kosong = sitasi racun → tolak, bukan "".
                raise ValueError("edge tanpa chunk_id (provenance wajib)")
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"kg:{self._workspace}:{doc_id}",))
                # Rebuild idempoten per-dokumen (D10, pola K9 chunks).
                await conn.execute(
                    "DELETE FROM kg_edges WHERE workspace = %s AND document_id = %s",
                    (self._workspace, doc_id))
                await conn.execute(
                    "DELETE FROM kg_entities WHERE workspace = %s AND document_id = %s",
                    (self._workspace, doc_id))
                n_ent = await self._insert_entities(conn, doc_id, entities)
                n_edge = await self._insert_edges(conn, doc_id, edges)
        return n_ent, n_edge

    async def _insert_entities(self, conn, doc_id: str,
                               entities: list[KgEntityIn]) -> int:
        # Dedup intra-batch last-wins: ON CONFLICT DO UPDATE crash bila 1 baris
        # kena 2x ("cannot affect row a second time"). Di-chunk per 500 baris.
        uniq = {e["norm"]: e for e in entities
                if (e.get("norm") or "").strip()}
        rows = [(
            self._workspace, doc_id, e["norm"], e.get("name") or e["norm"],
            e["type"], self._onto,
        ) for e in uniq.values()]
        for i in range(0, len(rows), _INSERT_CHUNK_ROWS):
            part = rows[i:i + _INSERT_CHUNK_ROWS]
            vals = ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(part))
            flat = [v for r in part for v in r]
            await conn.execute(
                f"INSERT INTO kg_entities (workspace, document_id, norm, name,"
                f" type, onto_version) VALUES {vals}"
                f" ON CONFLICT (document_id, norm) DO UPDATE SET"
                f" name = EXCLUDED.name, type = EXCLUDED.type,"
                f" onto_version = EXCLUDED.onto_version",
                flat)
        return len(rows)

    async def _insert_edges(self, conn, doc_id: str,
                            edges: list[KgEdgeIn]) -> int:
        uniq = {(e["src_norm"], e["dst_norm"], e["relation"]): e
                for e in edges}
        rows = [(
            self._workspace, doc_id, e["src_norm"], e["dst_norm"],
            e["relation"], e.get("confidence"),
            e.get("layer") or "L2",
            e.get("coref_resolved", True),
            e.get("access_level") or "public",
            e["chunk_id"], int(e.get("chunk_ord") or 0),
            e.get("chunk_row_id"), self._onto,
        ) for e in uniq.values()]
        for i in range(0, len(rows), _INSERT_CHUNK_ROWS):
            part = rows[i:i + _INSERT_CHUNK_ROWS]
            vals = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"]
                            * len(part))
            flat = [v for r in part for v in r]
            await conn.execute(
                f"INSERT INTO kg_edges (workspace, document_id, src_norm, dst_norm,"
                f" relation, confidence, layer, coref_resolved, access_level,"
                f" chunk_id, chunk_ord, chunk_row_id, onto_version)"
                f" VALUES {vals}"
                f" ON CONFLICT (document_id, src_norm, dst_norm, relation)"
                f" DO UPDATE SET confidence = EXCLUDED.confidence,"
                f" layer = EXCLUDED.layer,"
                f" coref_resolved = EXCLUDED.coref_resolved,"
                f" access_level = EXCLUDED.access_level,"
                f" chunk_id = EXCLUDED.chunk_id,"
                f" chunk_ord = EXCLUDED.chunk_ord,"
                f" chunk_row_id = EXCLUDED.chunk_row_id,"
                f" onto_version = EXCLUDED.onto_version",
                flat)
        return len(rows)

    async def delete_subgraph(self, *, doc_id: str) -> tuple[int, int]:
        """Return (n_entity, n_edge) — SAMA dengan upsert (kontrak ports)."""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                # Lock SAMA dengan upsert: cegah interleave
                # upsert.DELETE → delete.DELETE → upsert.INSERT (graph bangkit).
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"kg:{self._workspace}:{doc_id}",))
                cur = await conn.execute(
                    "DELETE FROM kg_edges WHERE workspace = %s AND document_id = %s",
                    (self._workspace, doc_id))
                n_edge = cur.rowcount or 0
                cur = await conn.execute(
                    "DELETE FROM kg_entities WHERE workspace = %s"
                    " AND document_id = %s",
                    (self._workspace, doc_id))
                return (cur.rowcount or 0), n_edge

    async def upsert_aliases(self, aliases: dict[str, str]) -> int:
        """Tulis alias workspace. Norm dinormalisasi ulang (sumber tunggal)."""
        pairs = [(normalize_entity(a), normalize_entity(c))
                 for a, c in (aliases or {}).items()]
        pairs = [(a, c) for a, c in pairs if a and c and a != c]
        if not pairs:
            return 0
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                vals = ",".join(["(%s,%s,%s)"] * len(pairs))
                flat = [v for a, c in pairs for v in (self._workspace, a, c)]
                await conn.execute(
                    "INSERT INTO kg_aliases (workspace, alias_norm,"
                    " canonical_norm) VALUES " + vals +
                    " ON CONFLICT (workspace, alias_norm) DO UPDATE SET"
                    " canonical_norm = EXCLUDED.canonical_norm",
                    flat)
        return len(pairs)

    async def list_aliases(self) -> dict[str, str]:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                cur = await conn.execute(
                    "SELECT alias_norm, canonical_norm FROM kg_aliases"
                    " WHERE workspace = %s",
                    (self._workspace,))
                return {r["alias_norm"]: r["canonical_norm"]
                        for r in await cur.fetchall()}

    async def resolve_seeds(self, seeds: list[str]) -> list[str]:
        """Norm → kanonis via alias → filter yang match entity. Stabil."""
        norms = sorted({n for s in seeds or []
                        if (n := normalize_entity(s))})
        if not norms:
            return []
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                cur = await conn.execute(
                    "SELECT alias_norm, canonical_norm FROM kg_aliases"
                    " WHERE workspace = %s AND alias_norm = ANY(%s)",
                    (self._workspace, norms))
                alias = {r["alias_norm"]: r["canonical_norm"]
                         for r in await cur.fetchall()}
                canon = sorted({alias.get(n, n) for n in norms})
                cur = await conn.execute(
                    "SELECT DISTINCT norm FROM kg_entities"
                    " WHERE workspace = %s AND norm = ANY(%s)"
                    " AND onto_version = %s",
                    (self._workspace, canon, self._onto))
                matched = {r["norm"] for r in await cur.fetchall()}
        return [n for n in canon if n in matched]

    async def graph_info(self) -> GraphInfo:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_workspace', %s, true)",
                    (self._workspace,))
                cur = await conn.execute(
                    "SELECT COUNT(*) AS n FROM kg_entities WHERE workspace = %s",
                    (self._workspace,))
                n_ent = (await cur.fetchone())["n"]
                cur = await conn.execute(
                    "SELECT COUNT(*) AS n FROM kg_edges WHERE workspace = %s",
                    (self._workspace,))
                n_edge = (await cur.fetchone())["n"]
        return {"extractor": self._model, "extractor_version": self._version,
                "onto_version": self._onto, "entity_count": n_ent,
                "edge_count": n_edge}

    # -- baca (retrieval) --

    def _access_clause(self, filters: dict[str, Any] | None,
                       ) -> tuple[str, list[Any]]:
        if not filters:
            return "", []
        bad = set(filters) - GRAPH_FILTER_KEYS
        if bad:
            raise ValueError(f"kunci filter graph tak dikenal: {sorted(bad)} "
                             f"(v0.8: {sorted(GRAPH_FILTER_KEYS)} saja)")
        # v1.0 G6: IN-list (kolom NOT NULL — tanpa COALESCE).
        val = filters["access_level"]
        levels = (list(val) if isinstance(val, (list, tuple, set)) else [val])
        return " AND e.access_level = ANY(%s)", [[str(v) for v in levels]]

    async def expand(self, seeds: list[str], *, k: int,
                     filters: dict[str, Any] | None = None,
                     max_hops: int = 2, fanout: int = 50) -> list[KgHit]:
        """Seed → BFS bounded 2 arah → evidence. Cap: ≤200 path global.

        Catatan cap: tiap query level di-LIMIT 200 per arah (fwd/bwd), lalu
        path global dipotong 200 (DESIGN §4: "edges/level ≤200 per arah").
        """
        if k < 1:
            raise ValueError(f"expand k wajib ≥ 1 (dapat {k})")
        if not 1 <= int(max_hops) <= 2:
            raise ValueError(f"expand max_hops wajib 1..2 (dapat {max_hops})")
        if not 1 <= int(fanout) <= 500:
            raise ValueError(f"expand fanout wajib 1..500 (dapat {fanout})")
        norms = await self.resolve_seeds(seeds or [])
        norms = norms[:_MAX_SEEDS]
        if not norms:
            return []
        access_sql, access_params = self._access_clause(filters)
        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.current_workspace', %s, true)",
                        (self._workspace,))
                    await conn.execute(
                        f"SET LOCAL statement_timeout = {int(self._timeout_ms)}")
                    found = await self._seed_lookup(conn, norms)
                    if not found:
                        return []
                    paths = await self._bfs(conn, list(found),
                                            max_hops=max_hops, fanout=fanout,
                                            access_sql=access_sql,
                                            access_params=access_params)
                    if not paths:
                        return []
                    # v1.0 G2: filter ACL DITERUSKAN ke fetch provenance
                    # (defense-in-depth atas skew edge-vs-chunk/doc, mis.
                    # ACL berubah pasca-ingest atau data pra-backfill-021).
                    acl = (filters or {}).get("access_level")
                    if acl is not None and not isinstance(
                            acl, (list, tuple, set)):
                        acl = [acl]
                    names = await self._fetch_names(
                        conn, {n for p in paths for n in p["nodes"]})
                    titles = await self._fetch_titles(
                        conn, {e["document_id"] for p in paths
                               for e in p["edges"]}, acl=acl)
                    contents = await self._fetch_contents(
                        conn, {e["chunk_row_id"] for p in paths
                               for e in p["edges"]
                               if e.get("chunk_row_id")}, acl=acl)
                    return self._to_hits(paths, names, titles, contents, k=k,
                                         require_acl=acl is not None)
        except pg_errors.QueryCanceled as exc:
            raise TimeoutError("expand graph timeout") from exc

    async def _seed_lookup(self, conn, norms: list[str]) -> set[str]:
        cur = await conn.execute(
            "SELECT DISTINCT norm FROM kg_entities"
            " WHERE workspace = %s AND norm = ANY(%s) AND onto_version = %s",
            (self._workspace, norms, self._onto))
        return {r["norm"] for r in await cur.fetchall()}

    async def _bfs(self, conn, frontier: list[str], *, max_hops: int,
                   fanout: int, access_sql: str,
                   access_params: list[Any]) -> list[dict]:
        """BFS bounded 2 arah. Return paths: [{nodes, edges, score}].

        Level-1 = path 1 edge dari seed; level-2 = extend path L1 yang
        ujungnya di frontier (stitching via node tengah). Cap total path
        agar hub tak meledakkan memori.
        """
        visited: set[str] = set(frontier)
        paths: list[dict] = []
        open_paths: dict[str, list[dict]] = {}  # end-node → paths
        for _ in range(max(0, max_hops)):
            if not frontier or len(visited) >= _MAX_NODES:
                break
            rows = await self._level(conn, sorted(frontier), fanout=fanout,
                                     access_sql=access_sql,
                                     access_params=access_params)
            per_node: dict[str, int] = {}
            for r in rows:
                per_node[r["from_norm"]] = per_node.get(r["from_norm"], 0) + 1
            nxt: list[str] = []
            for r in rows:
                if len(paths) >= _GLOBAL_EDGE_CAP:
                    break
                deg_est = per_node[r["from_norm"]]
                # log1p(1) < 1 → tanpa clamp skor MELEDAK di node derajat-1.
                penalty = min(1.0, 1.0 / math.log1p(deg_est))
                conf = r["confidence"]
                if conf is None:
                    conf = _DEFAULT_CONF
                base = open_paths.get(r["from_norm"], [None])
                for parent in base[:4]:  # cap extend/path agar tak eksponensial
                    if parent is None:
                        nodes = [r["from_norm"], r["to_norm"]]
                        edges = [dict(r)]
                        score = float(conf) * penalty
                    else:
                        if r["to_norm"] in parent["nodes"]:
                            continue  # tolak sikel A→B→A
                        nodes = parent["nodes"] + [r["to_norm"]]
                        edges = parent["edges"] + [dict(r)]
                        score = parent["score"] * float(conf) * penalty \
                            * _HOP2_DISCOUNT
                    path = {"nodes": nodes, "edges": edges, "score": score}
                    paths.append(path)
                    open_paths.setdefault(r["to_norm"], []).append(path)
                if r["to_norm"] not in visited and len(visited) < _MAX_NODES:
                    visited.add(r["to_norm"])
                    nxt.append(r["to_norm"])
            if not nxt:
                break
            frontier = nxt
        # Sort deterministik penuh (skor seri → hops → id edge) agar 2 run sama.
        paths.sort(key=lambda p: (
            -p["score"], len(p["edges"]),
            [e["id"] for e in p["edges"]]))
        return paths

    async def _level(self, conn, frontier: list[str], *, fanout: int,
                     access_sql: str, access_params: list[Any]) -> list[dict]:
        """1 level BFS 2 arah (fwd + bwd), LATERAL anti-hub, tanpa ORDER BY.

        Cap: LIMIT 200 PER ARAH per level (bukan 200 global — lihat expand).
        Determinisme datang dari `sorted(frontier)` + LIMIT stabil per-node;
        urutan akhir path diputus tiebreak di `_bfs` (bukan di sini).
        """
        out: list[dict] = []
        for direction in ("fwd", "bwd"):
            join_col = "src_norm" if direction == "fwd" else "dst_norm"
            cur = await conn.execute(
                "SELECT f.n AS from_norm, e.id, e.src_norm, e.dst_norm,"
                " e.relation, e.confidence, e.layer, e.coref_resolved,"
                " e.document_id, e.chunk_id, e.chunk_ord, e.chunk_row_id,"
                " e.access_level"
                " FROM unnest(%s::text[]) AS f(n) CROSS JOIN LATERAL ("
                f"  SELECT id, src_norm, dst_norm, relation, confidence,"
                f"   layer, coref_resolved, document_id, chunk_id,"
                f"   chunk_ord, chunk_row_id, access_level"
                f"  FROM kg_edges e WHERE workspace = %s AND {join_col} = f.n"
                f"  AND onto_version = %s{access_sql}"
                f"  LIMIT {int(fanout)}"
                f") e LIMIT {_GLOBAL_EDGE_CAP}",
                (frontier, self._workspace, self._onto, *access_params))
            for r in await cur.fetchall():
                d = dict(r)
                # Arah maju: frontier=src, tetangga=dst. Arah balik:
                # frontier=dst (muncul sebagai dst), tetangga=src.
                d["to_norm"] = (d["dst_norm"] if direction == "fwd"
                                else d["src_norm"])
                out.append(d)
        # Dedup (id, arah): edge SAMA bisa valid muncul 2x — sekali fwd dari
        # src, sekali bwd dari dst (keduanya frontier). Kunci id saja salah
        # (membuang traversal sah); kunci (id, from) benar.
        seen: set[tuple[int, str]] = set()
        uniq: list[dict] = []
        for r in out:
            key = (r["id"], r["from_norm"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        return uniq

    async def _fetch_names(self, conn, norms: set[str]) -> dict[str, str]:
        if not norms:
            return {}
        cur = await conn.execute(
            "SELECT DISTINCT ON (norm) norm, name FROM kg_entities"
            " WHERE workspace = %s AND norm = ANY(%s)"
            " ORDER BY norm, document_id",
            (self._workspace, list(norms)))
        return {r["norm"]: r["name"] for r in await cur.fetchall()}

    async def _fetch_titles(self, conn, doc_ids: set,
                              acl: list | None = None) -> dict[str, str]:
        if not doc_ids:
            return {}
        if acl is None:
            cur = await conn.execute(
                "SELECT id, title FROM documents WHERE workspace = %s AND id = ANY(%s)",
                (self._workspace, list(doc_ids)))
        else:
            # COALESCE: dokumen lama tanpa kunci = public (kalah-hati-hati?
            # TIDAK — ini konsisten dengan filter edge: edge pra-G1 tertulis
            # public; backfill 021 menyelaraskan. Tanpa COALESCE, provenance
            # public hilang total.)
            cur = await conn.execute(
                "SELECT id, title FROM documents WHERE workspace = %s AND id = ANY(%s)"
                " AND COALESCE(meta->>'access_level', 'public') = ANY(%s)",
                (self._workspace, list(doc_ids), [str(a) for a in acl]))
        return {str(r["id"]): r["title"] for r in await cur.fetchall()}

    async def _fetch_contents(self, conn, row_ids: set,
                              acl: list | None = None) -> dict[str, str]:
        """Isi chunk provenance per chunk_row_id (G2 union lengkap, 1 query)."""
        if not row_ids:
            return {}
        if acl is None:
            cur = await conn.execute(
                "SELECT id, content FROM chunks WHERE workspace = %s AND id = ANY(%s)",
                (self._workspace, list(row_ids)))
        else:
            cur = await conn.execute(
                "SELECT id, content FROM chunks WHERE workspace = %s AND id = ANY(%s)"
                " AND COALESCE(meta->>'access_level', 'public') = ANY(%s)",
                (self._workspace, list(row_ids), [str(a) for a in acl]))
        return {str(r["id"]): r["content"] for r in await cur.fetchall()}

    def _to_hits(self, paths: list[dict], names: dict[str, str],
                 titles: dict[str, str], contents: dict[str, str],
                 *, k: int, require_acl: bool = False) -> list[KgHit]:
        from app.rag.citations import gpath_id

        hits: list[KgHit] = []
        for p in paths[:k]:
            if require_acl:
                # v1.0 G2: hop tak-lolos gugurkan SELURUH path (bukan
                # konten-kosong-diam). chunk_row_id NULL (edge lawas) =
                # tak terverifikasi → lolos (filter edge sudah diterapkan).
                drop = False
                for e in p["edges"]:
                    if str(e["document_id"]) not in titles:
                        drop = True
                        break
                    if e.get("chunk_row_id") and str(
                            e["chunk_row_id"]) not in contents:
                        drop = True
                        break
                if drop:
                    continue
            triples = []
            for e in p["edges"]:
                s = names.get(e["src_norm"], e["src_norm"])
                o = names.get(e["dst_norm"], e["dst_norm"])
                triples.append((s, e["relation"], o))
            first = p["edges"][0]
            doc_id = str(first["document_id"])
            hits.append({
                "chunk_id": gpath_id([e["id"] for e in p["edges"]],
                                     VERBALIZE_VERSION),
                "doc_id": doc_id,
                "content": verbalize_path(triples),
                "source": titles.get(doc_id, doc_id),
                "page": None,
                "score": p["score"],
                "hops": len(p["edges"]),
                "metadata": {
                    "kind": "graph",
                    "provenance": [e["chunk_id"] for e in p["edges"]],
                    "provenance_contents": [
                        contents.get(str(e["chunk_row_id"]), "")
                        if e.get("chunk_row_id") else ""
                        for e in p["edges"]],
                    "doc_ids": [str(e["document_id"]) for e in p["edges"]],
                    "chunk_row_ids": [
                        str(e["chunk_row_id"]) if e.get("chunk_row_id")
                        else None for e in p["edges"]],
                    "confidence": min(
                        (e["confidence"] if e["confidence"] is not None
                         else _DEFAULT_CONF) for e in p["edges"]),
                    "layers": [e["layer"] for e in p["edges"]],
                    "relations": [e["relation"] for e in p["edges"]],
                    "edge_ids": [e["id"] for e in p["edges"]],
                    "extractor": self._model,
                    "onto_version": self._onto,
                },
            })
        return hits
