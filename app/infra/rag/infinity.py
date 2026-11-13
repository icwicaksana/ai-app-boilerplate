"""Klien Infinity v2 (D20): `EmbeddingsPort` + `RerankerPort` di 1 service.

Kontrak perilaku (beku):
- Path v2 = `/embeddings` + `/rerank` (BUKAN `/v1/*` — 404, addendum A1.1).
- Embed GAGAL (timeout/5xx/koneksi) → `EmbeddingUnavailableError` (D6).
  DILARANG fallback beda-model — 503 eksplisit, bukan vektor campur.
  Pengecualian v0.7.1 (F5b): fallback MODEL-IDENTIK (nama + dims
  divalidasi saat wiring) via `FallbackEmbeddings` — D6 dipertahankan.
- Rerank GAGAL APAPUN → `RerankError` (timeout = builtin `TimeoutError`,
  sisanya `RerankError`); pipeline degradasi ke RRF + flag (P0-2).
"""

from __future__ import annotations

import logging

import httpx

from app.core.errors import (
    EmbeddingUnavailableError,
    RerankError,
)

log = logging.getLogger("app.infra.rag.infinity")

class InfinityError(RerankError):
    """Alias kompatibel (dulu error rerank non-timeout). Pakai RerankError."""


def _client(base_url: str, timeout_ms: int,
            transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(timeout_ms / 1000.0),
        transport=transport,
    )


class InfinityEmbeddings:
    """`EmbeddingsPort` via Infinity. Satu instance per worker/proses."""

    def __init__(self, base_url: str, model: str, dims: int,
                 timeout_ms: int = 1500, version: str = "torch-fp32",
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url
        self._model = model
        self._dims = dims
        self._version = version
        self._client = _client(base_url, timeout_ms, transport)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dims(self) -> int:
        return self._dims

    @property
    def version(self) -> str:
        return self._version

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            resp = await self._client.post(
                "/embeddings", json={"model": self._model, "input": texts})
            resp.raise_for_status()
            data = resp.json()["data"]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            # HTTPError luas (Read/Write/PoolTimeout ikut) + TypeError skema
            # aneh → 503 eksplisit, bukan 500 mentah (P12).
            raise EmbeddingUnavailableError(
                f"Embedding gagal ({type(exc).__name__}).") from exc
        vecs = [row["embedding"] for row in data]
        if not vecs or len(vecs) != len(texts):
            raise EmbeddingUnavailableError(
                "Respons embedding jumlah salah.")
        if self._dims and any(len(v) != self._dims for v in vecs):
            raise EmbeddingUnavailableError(
                "Respons embedding dimensi salah (model berubah?).")
        return vecs

    async def ping(self) -> bool:
        """Health murah (tanpa inferensi). Tak pernah raise."""
        try:
            resp = await self._client.get("/health")
            return 200 <= resp.status_code < 300
        except Exception:  # noqa: BLE001 - ping = boolean, bukan error
            return False

    async def probe_dims(self) -> int:
        """Deteksi dimensi model (embed 1 token) + kunci ke instance.

        Dipanggil lifespan startup (fail-fast bila service down).
        """
        vecs = await self.embed_texts(["ok"])
        self._dims = len(vecs[0])
        return self._dims

    async def aclose(self) -> None:
        await self._client.aclose()


class FallbackEmbeddings:
    """`EmbeddingsPort` primer → fallback MODEL-IDENTIK (v0.7.1 F5b).

    Failover per-request saat primer raise `EmbeddingUnavailableError`
    (timeout/5xx/koneksi); keduanya gagal = raise error fallback. Recovery
    otomatis: tiap request mencoba primer dulu (`on_fallback` reset saat
    primer pulih). Kesamaan model DIJAMIN factory (nama + dims), bukan
    di sini — wrapper ini hanya failover mekanis.
    """

    has_fallback = True

    def __init__(self, primary: InfinityEmbeddings,
                 fallback: InfinityEmbeddings) -> None:
        self._primary = primary
        self._fallback = fallback
        self._on_fallback = False

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def dims(self) -> int:
        return self._primary.dims

    @property
    def version(self) -> str:
        return self._primary.version

    @property
    def on_fallback(self) -> bool:
        return self._on_fallback

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        try:
            vecs = await self._primary.embed_texts(texts)
        except EmbeddingUnavailableError as exc:
            log.warning("embed primer gagal (%s) — pakai fallback", exc)
            vecs = await self._fallback.embed_texts(texts)
            self._on_fallback = True
            return vecs
        self._on_fallback = False
        return vecs

    async def ping(self) -> bool:
        """True bila SALAH SATU hidup (health gabungan)."""
        if await self._primary.ping():
            return True
        return await self._fallback.ping()

    async def probe_dims(self) -> int:
        return await self._primary.probe_dims()

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()


class InfinityReranker:
    """`RerankerPort` via Infinity. Respons v2: results[{index, relevance_score}]."""

    def __init__(self, base_url: str, model: str, timeout_ms: int = 2500,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._model = model
        self._client = _client(base_url, timeout_ms, transport)

    @property
    def model(self) -> str:
        return self._model

    async def rerank(self, query: str, documents: list[str], *,
                     top_n: int) -> list[dict]:
        if not documents:
            return []
        try:
            resp = await self._client.post("/rerank", json={
                "model": self._model, "query": query,
                "documents": documents, "top_n": top_n})
            resp.raise_for_status()
            results = resp.json()["results"]
        except httpx.TimeoutException as exc:
            raise TimeoutError("rerank timeout") from exc
        except httpx.HTTPError as exc:
            # SEMUA gagal HTTP (connect/read/pool/5xx) → RerankError agar
            # pipeline degradasi (RRF), bukan 500 (P0-2, P12).
            raise RerankError(
                f"Rerank gagal ({type(exc).__name__}).") from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise RerankError("Respons rerank bentuk tak dikenal.") from exc
        try:
            return [{"index": int(r["index"]),
                     "score": float(r["relevance_score"])} for r in results]
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError("Respons rerank bentuk tak dikenal.") from exc

    async def ping(self) -> bool:
        """Health murah (service-level, tanpa inferensi). Tak pernah raise."""
        try:
            resp = await self._client.get("/health")
            return 200 <= resp.status_code < 300
        except Exception:  # noqa: BLE001 - ping = boolean, bukan error
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
