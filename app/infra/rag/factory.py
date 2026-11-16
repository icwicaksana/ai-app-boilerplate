"""Wiring RAG (D2, P2, D6): klien bersama (lifespan) + retriever per-request.

- Lifespan: `init_rag()` membangun clients + probe dims + validasi backend
  (fail-fast dengan pesan aksi) → simpan di `app.state`.
- Request: `retriever_for_tenant()` mengikat tenant dari JWT → `HybridRetriever`.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.errors import RagBackendMismatchError
from app.infra.rag.infinity import InfinityEmbeddings, InfinityReranker
from app.infra.rag.pgvector import PgVectorStore
from app.infra.rag.qdrant import QdrantStore, collection_name
from app.infra.rag.tokenizer import BgeTokenizer
from app.rag.pipeline import HybridRetriever


class RagClients:
    """Bundle klien bersama (1 per proses). Dibangun `init_rag()`."""

    def __init__(self, *, embed, rerank, tok, qdrant, dims: int,
                 extractor=None) -> None:
        self.embed = embed
        self.rerank = rerank
        self.tok = tok
        self.qdrant = qdrant  # AsyncQdrantClient | None
        self.dims = dims
        # EntityExtractor | None (jasa model tenant-agnostik, pola embed).
        # GraphStore TIDAK di sini: tenant-bound, dibangun per-request
        # di retriever_for_tenant (pola VectorStore, D2).
        self.extractor = extractor

    async def aclose(self) -> None:
        if self.embed is not None:
            await self.embed.aclose()
        if self.rerank is not None:
            await self.rerank.aclose()
        if self.qdrant is not None:
            await self.qdrant.close()
        if self.extractor is not None and hasattr(self.extractor, "aclose"):
            await self.extractor.aclose()


async def init_rag(settings: Settings, pool,
                   transport: httpx.AsyncBaseTransport | None = None,
                   ) -> RagClients | None:
    """Bangun + validasi stack RAG. None bila RAG nonaktif (embed URL kosong).

    Fail-fast (raise RuntimeError aksi) bila dikonfigurasi tapi rusak:
    embed down | rerank down | qdrant down | koleksi/index tak cocok pin.

    Cold-start: probe dims di-retry ~2 menit (Infinity pertama kali bisa
    30–60 dtk memuat model; tanpa ini worker/API mati saat stack baru up).
    """
    import asyncio
    import logging

    log = logging.getLogger(__name__)
    if not (settings.RAG_EMBED_BASE_URL or "").strip():
        return None
    embed = InfinityEmbeddings(
        settings.RAG_EMBED_BASE_URL, settings.RAG_EMBED_MODEL, dims=0,
        timeout_ms=settings.RAG_EMBED_TIMEOUT_MS, transport=transport)
    rerank = InfinityReranker(
        settings.RAG_RERANK_BASE_URL, settings.RAG_RERANK_MODEL,
        timeout_ms=settings.RAG_RERANK_TIMEOUT_MS, transport=transport)
    from app.core.errors import EmbeddingUnavailableError

    last_exc: Exception | None = None
    for attempt, wait in enumerate([5, 10, 20, 30, 30, 30]):
        try:
            if not await embed.ping():
                raise EmbeddingUnavailableError("ping embed gagal")
            dims = await embed.probe_dims()
            if not await rerank.ping():
                raise EmbeddingUnavailableError("ping rerank gagal")
            break
        except Exception as exc:  # noqa: BLE001 - kumpulkan, retry cold-start
            last_exc = exc
            log.warning("init_rag probe %d/6 gagal (%s) — retry %ds",
                        attempt + 1, type(exc).__name__, wait)
            await asyncio.sleep(wait)
    else:
        await embed.aclose()
        await rerank.aclose()
        raise RuntimeError(
            "Service embedding tak terjangkau setelah ~2 menit "
            f"({settings.RAG_EMBED_BASE_URL}/health, terakhir: "
            f"{type(last_exc).__name__}). Jalankan "
            "`docker compose -f docker-compose.embed.yml up -d` di Infra/.")

    # Fallback MODEL-IDENTIK (F5b): nama wajib sama persis (fail-fast =
    # miskonfigurasi operator); dims dibuktikan via probe. Fallback DOWN
    # saat start = warning + lanjut tanpa fallback (primer sehat cukup).
    fb_url = (settings.RAG_EMBED_FALLBACK_URL or "").strip()
    if fb_url:
        fb_model = (settings.RAG_EMBED_FALLBACK_MODEL or "").strip()
        if fb_model != settings.RAG_EMBED_MODEL:
            await embed.aclose()
            await rerank.aclose()
            raise RuntimeError(
                f"RAG_EMBED_FALLBACK_MODEL ({fb_model!r}) != RAG_EMBED_MODEL "
                f"({settings.RAG_EMBED_MODEL!r}) — fallback beda-model "
                "DILARANG (D6, vektor campur). Samakan atau kosongkan URL.")
        from app.infra.rag.infinity import FallbackEmbeddings

        fb = InfinityEmbeddings(
            fb_url, fb_model, dims=0,
            timeout_ms=settings.RAG_EMBED_TIMEOUT_MS, transport=transport)
        try:
            fb_dims = await fb.probe_dims()
        except Exception as exc:  # noqa: BLE001 - fallback opsional
            log.warning("fallback embed down saat start (%s) — lanjut "
                        "tanpa fallback", type(exc).__name__)
            await fb.aclose()
        else:
            if fb_dims != dims:
                await fb.aclose()
                await embed.aclose()
                await rerank.aclose()
                raise RuntimeError(
                    f"Fallback embed dims={fb_dims} != primer {dims} — "
                    "tolak start (D6).")
            embed = FallbackEmbeddings(embed, fb)
            log.info("embed fallback aktif (model=%s dims=%d)", fb_model,
                     dims)

    qdrant_client = None
    try:
        if settings.RAG_BACKEND == "qdrant":
            from qdrant_client import AsyncQdrantClient

            qdrant_client = AsyncQdrantClient(
                url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY,
                timeout=10)
            try:
                await qdrant_client.get_collections()
            except Exception as exc:
                raise RuntimeError(
                    f"Qdrant tak terjangkau ({settings.QDRANT_URL}). Jalankan "
                    "`docker compose --profile qdrant up -d qdrant` di Infra/. "
                    f"Detail: {type(exc).__name__}") from exc
            probe = QdrantStore(qdrant_client,
                                collection_name(dims, settings.RAG_EMBED_MODEL),
                                tenant="__probe__")
            await probe.ensure_collection()
            try:
                col = await qdrant_client.get_collection(probe._collection)
                size = col.config.params.vectors.size  # type: ignore[union-attr]
            except Exception as exc:
                raise RuntimeError(
                    f"Qdrant collection tak terbaca: {type(exc).__name__}") from exc
            if size != dims and not settings.RAG_ALLOW_MIXED:
                raise RuntimeError(
                    f"Qdrant collection dim={size} != embed dim={dims}. "
                    "Reindex eksplisit (D4) atau set RAG_ALLOW_MIXED=1 sementara.")
        else:
            await _validate_pg_triples(settings, pool, dims)
    except Exception:
        # Semua resource lifespan ditutup di 1 tempat (anti-bocor, P1).
        if qdrant_client is not None:
            await qdrant_client.close()
        await embed.aclose()
        await rerank.aclose()
        raise

    tok = BgeTokenizer(settings.RAG_EMBED_MODEL,
                       cache_dir=settings.RAG_HF_CACHE)
    try:
        extractor = _build_extractor(settings)
    except Exception:
        # Gagal D6 ekstraktor = tutup SEMUA seperti backend gagal (P0 F2).
        if qdrant_client is not None:
            await qdrant_client.close()
        await embed.aclose()
        await rerank.aclose()
        raise
    return RagClients(embed=embed, rerank=rerank, tok=tok,
                      qdrant=qdrant_client, dims=dims,
                      extractor=extractor)


# Model masa depan yang DIKENAL tapi belum diimplementasi (bedakan error).
_KNOWN_FUTURE_EXTRACTORS = frozenset({"fastino/gliner2.5-multi-v1"})


def _build_extractor(settings: Settings):
    """Pilih EntityExtractor dari pin (D6). None bila GRAPH_ENABLED=false.

    F2: hanya `stub-deterministik`. F4 menambah L1/L2/L3 nyata.
    Pin tak dikenal = RuntimeError aksi (dilarang fallback beda-model diam-diam).
    """
    if not settings.GRAPH_ENABLED:
        return None
    want_model = (settings.GRAPH_EXTRACTOR_MODEL or "").strip()
    want_ver = (settings.GRAPH_EXTRACTOR_VERSION or "").strip()
    if want_model == "stub-deterministik":
        from app.infra.rag.extract.stub import StubExtractor

        ext = StubExtractor()
    elif want_model in _KNOWN_FUTURE_EXTRACTORS:
        raise RuntimeError(
            f"GRAPH_EXTRACTOR_MODEL={want_model!r} dikenal tapi BELUM tersedia"
            " (ekstraktor nyata = v0.8 F4). Untuk F2 pakai "
            "'stub-deterministik'.")
    else:
        raise RuntimeError(
            f"GRAPH_EXTRACTOR_MODEL={want_model!r} tak dikenal "
            "(v0.8 F2: hanya 'stub-deterministik'; L1/L2/L3 nyata di F4).")
    if want_ver and ext.version != want_ver:
        raise RuntimeError(
            f"GRAPH_EXTRACTOR_VERSION pin={want_ver!r} != ekstraktor "
            f"{ext.version!r} (D6). Samakan atau kosongkan pin.")
    return ext


async def _validate_pg_triples(settings: Settings, pool, dims: int) -> None:
    """D6: tolak start bila index PG tak cocok pin (kecuali ALLOW_MIXED/reindex).

    Versi embed hanya divalidasi bila `RAG_EMBED_VERSION` diisi (P1-9):
    default client ("torch-fp32") adalah label deployment, bukan fakta engine.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT DISTINCT embedding_model, embedding_dim,"
            " embedding_version FROM chunks LIMIT 5")
        triples = await cur.fetchall()
    want_ver = (settings.RAG_EMBED_VERSION or "").strip()
    for t in triples:
        same = (t["embedding_model"] == settings.RAG_EMBED_MODEL
                and t["embedding_dim"] == dims
                and (not want_ver or t["embedding_version"] == want_ver))
        if not same and not settings.RAG_ALLOW_MIXED:
            raise RuntimeError(
                f"Index PG ({t['embedding_model']}/{t['embedding_dim']}/"
                f"{t['embedding_version']}) != pin RAG_EMBED "
                f"({settings.RAG_EMBED_MODEL}/{dims}/{want_ver or '*'}). "
                "Reindex eksplisit (D4) atau set RAG_ALLOW_MIXED=1 sementara.")


def retriever_for_tenant(settings: Settings, clients: RagClients | None,
                         pool, *, tenant: str) -> HybridRetriever:
    """Bangun `HybridRetriever` terikat tenant (D2). Dipanggil per-request
    dari `ThreadIdentity` JWT — BUKAN dari body (dependencies)."""
    if clients is None:
        from app.core.errors import EmbeddingUnavailableError
        raise EmbeddingUnavailableError("RAG nonaktif (RAG_EMBED_BASE_URL kosong).")
    if settings.RAG_BACKEND == "qdrant":
        if clients.qdrant is None:
            raise RagBackendMismatchError("RAG_BACKEND=qdrant tapi klien "
                                          "Qdrant tak ada (lifespan gagal?).")
        store = QdrantStore(
            clients.qdrant,
            collection_name(clients.dims, settings.RAG_EMBED_MODEL),
            tenant=tenant, model=settings.RAG_EMBED_MODEL,
            dim=clients.dims)
    else:
        store = PgVectorStore(
            pool, tenant=tenant, model=settings.RAG_EMBED_MODEL,
            dim=clients.dims)
    return HybridRetriever(
        embed=clients.embed, rerank=clients.rerank, store=store,
        tok=clients.tok, dense_top=settings.RAG_DENSE_TOP,
        lexical_top=settings.RAG_LEXICAL_TOP, rrf_k=settings.RAG_RRF_K,
        candidate_top=settings.RAG_CANDIDATE_TOP,
        final_top=settings.RAG_FINAL_TOP,
        query_max_tok=settings.RAG_QUERY_MAX_TOK,
        doc_max_tok=settings.RAG_DOC_MAX_TOK,
        pair_max_tok=settings.RAG_PAIR_MAX_TOK,
        score_floor=settings.RAG_RERANK_SCORE_FLOOR,
        stem=settings.RAG_STEMMER)
