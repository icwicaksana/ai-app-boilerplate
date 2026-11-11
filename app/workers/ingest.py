"""Worker ingest RAG (D12): Postgres-queue + lease + stage machine.

Entrypoint: `python -m app.workers.ingest` (proses terpisah dari API).

Aturan keras (T11/D12):
- Pool SENDIRI (`WORKER_DB_POOL_*`, statement_timeout 120s). Klaim = transaksi
  pendek (<100ms); DILARANG `await http_*()` di dalam `pool.connection()`.
- Lease 120s + heartbeat 30s; shutdown melepas lease (JANGAN hapus job, F-03).
- Retry: `5s*2^attempts + jitter`, cap 5 mnt, max 5x → dead (DLQ).
  Terminal langsung: UNSUPPORTED_MEDIA, OCR_REQUIRED (butuh aksi klien),
  OCR_FAILED (setelah 1x), BLOB_GONE, EMPTY_DOCUMENT, DOCUMENT_GONE.
  (FILE_TOO_LARGE ditolak API/413 — tak sampai worker.)
- Progress: SoR Postgres (`stage/progress`) + ekor Valkey best-effort
  (`ingest:{tenant}:{job_id}`, TTL 3600s).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import signal
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("app.workers.ingest")

EMBED_BATCH = 32
HEARTBEAT_SECONDS = 30
PROGRESS_TTL_SECONDS = 3600
PROGRESS_MAXLEN = 200
OCR_CONF_THRESHOLD = 0.6  # di bawah ini → meta low_ocr (downrank, D9)


# ---------------------------------------------------------------- pure helpers

def compute_backoff(attempts: int, *, base: float = 5.0,
                    cap: float = 300.0) -> float:
    """Detik tunda: `base*2^attempts + jitter(0-30%)`, cap 5 mnt (F-04)."""
    raw = min(base * (2 ** max(0, attempts)), cap)
    return raw + random.uniform(0, raw * 0.3)


def normalize_vector(vec: list[float]) -> list[float]:
    """Normalisasi L2 bila belum (≈1.0 = lewati, T02 terverifikasi spike)."""
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        raise _TerminalInput("BAD_VECTOR", "vektor nol dari embedder")
    if 0.999 < norm < 1.001:
        return vec
    return [x / norm for x in vec]


def table_to_factoid(sheet: str | None, headers: list[str],
                     row: list[str], *, prefix: str = "") -> str:
    """Baris tabel → 1 kalimat faktual (preset factoid, D8)."""
    cells = " | ".join(f"{h}: {v}" for h, v in zip(headers, row)
                       if (v or "").strip())
    head = f"[{prefix}] " if prefix else ""
    if sheet:
        head += f"[Sheet: {sheet}] "
    return f"{head}{cells}".strip()


class _OcrRequired(Exception):
    """Kontrol alur: PDF pindai + ocr!=on → dead + minta aksi klien (D10)."""


class _TerminalInput(Exception):
    """Input deterministik-buruk (traversal, vektor-nol): retry sia-sia (P18)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _LeaseLost(Exception):
    """Lease dicuri worker lain: BERHENTI tanpa fail_job (pemilik baru urus)."""


def classify_error(exc: Exception, attempts: int) -> tuple[bool, str, str]:
    """(boleh_retry, kode, pesan) — F-04/D12. Import lokal (ringan)."""
    from app.core.errors import (EmbeddingUnavailableError, OcrFailedError,
                                 UnsupportedMediaError)

    if isinstance(exc, _OcrRequired):
        return False, "OCR_REQUIRED", str(exc)
    if isinstance(exc, _TerminalInput):
        return False, exc.code, str(exc)
    if isinstance(exc, FileNotFoundError):
        # Blob hilang dari disk (cleanup manual / storage rusak): retry tak
        # membantu → terminal dengan kode jelas (bukan WORKER_ERROR samar).
        return False, "BLOB_GONE", str(exc)[-300:]
    if isinstance(exc, UnsupportedMediaError):
        return False, "UNSUPPORTED_MEDIA", str(exc)
    if isinstance(exc, OcrFailedError):
        retry = attempts < 2  # 1x retry lalu DLQ (D12)
        return retry, "OCR_FAILED", str(exc)
    if isinstance(exc, EmbeddingUnavailableError):
        return True, "EMBED_TIMEOUT", str(exc)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True, "UPSTREAM_TIMEOUT", f"{type(exc).__name__}: {exc}"
    try:
        import httpx

        if isinstance(exc, httpx.HTTPError):
            return True, "UPSTREAM_HTTP", f"{type(exc).__name__}: {exc}"
    except ImportError:  # pragma: no cover
        pass
    return True, "WORKER_ERROR", f"{type(exc).__name__}: {exc}"[:500]


# ---------------------------------------------------------------- konteks

@dataclass
class WorkerCtx:
    settings: object
    pool: object
    worker_id: str
    embed: object
    tok: object
    qdrant: object | None
    cache: object | None
    parsers: dict
    ocr: object


def build_parsers(settings) -> tuple[dict, object]:
    """Parser per MIME + engine OCR. Import docling lazy (berat)."""
    from app.infra.rag.docling_parser import DoclingParser
    from app.infra.rag.tesseract import TesseractEngine
    from app.infra.rag.xlsx import XlsxParser
    from app.rag.parsers.markdown import MarkdownParser
    from app.rag.parsers.sniff import DOCX, MD, PDF, PPTX, TXT, XLSX

    docling = DoclingParser()
    md = MarkdownParser()
    xlsx = XlsxParser(max_cells=settings.RAG_MAX_XLSX_CELLS)
    parsers = {PDF: docling, DOCX: docling, PPTX: docling,
               MD: md, TXT: md, XLSX: xlsx}
    return parsers, TesseractEngine()


async def build_ctx(settings, pool, *, worker_id: str) -> WorkerCtx:
    """Bangun ctx worker (embed + tok + qdrant? + cache? + parser)."""
    from app.infra.cache import CacheClient
    from app.infra.rag.factory import init_rag
    from app.infra.rag_migrate import apply_rag_migration

    await apply_rag_migration(pool)  # idempotent (entrypoint mana pun aman)
    clients = await init_rag(settings, pool)
    if clients is None:
        raise RuntimeError("Worker butuh RAG aktif (RAG_EMBED_BASE_URL).")
    cache = None
    if (settings.CACHE_URL or "").strip():
        cache = CacheClient.from_url(settings.CACHE_URL,
                                     socket_timeout=2.0)
        try:
            await cache.open()
        except Exception as exc:
            log.warning("cache worker tak terjangkau (%s) — lanjut tanpa ekor",
                        type(exc).__name__)
            cache = None
    parsers, ocr = build_parsers(settings)
    return WorkerCtx(settings=settings, pool=pool, worker_id=worker_id,
                     embed=clients.embed, tok=clients.tok,
                     qdrant=clients.qdrant, cache=cache,
                     parsers=parsers, ocr=ocr)


# ---------------------------------------------------------------- progress tail

_PROGRESS_LUA = """
redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[4], '*',
           'event', ARGV[1], 'stage', ARGV[2], 'progress', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


async def publish(ctx: WorkerCtx, tenant: str, job_id: int, event: str,
                  stage: str, progress: int) -> None:
    """Ekor Valkey best-effort (gagal = skip + log; SoR tetap Postgres)."""
    if ctx.cache is None:
        return
    try:
        await ctx.cache.eval_int(
            _PROGRESS_LUA, [f"ingest:{tenant}:{job_id}"],
            [event, stage, str(progress), str(PROGRESS_MAXLEN),
             str(PROGRESS_TTL_SECONDS)])
    except Exception as exc:
        log.debug("ekor progress gagal (best-effort): %s", exc)


# ---------------------------------------------------------------- process job

def _read_blob(settings, source_uri: str) -> bytes:
    """Baca blob dengan guard traversal (wajib di dalam RAG_STORAGE_DIR).

    NOTE Windows: `Path.resolve()` di mesin ini menggandakan separator
    (quirk realpath + junction, temuan uji worker) — pakai abspath +
    commonpath (pendekatan standar, tanpa resolve symlink) + tolak symlink
    eksplisit per-segmen (P3).
    """
    base = os.path.abspath(settings.RAG_STORAGE_DIR)
    target = (os.path.abspath(os.path.join(base, source_uri))
              if not os.path.isabs(source_uri)
              else os.path.abspath(source_uri))
    try:
        inside = os.path.commonpath([base]) == os.path.commonpath([base, target])
    except ValueError:
        inside = False  # drive berbeda = di luar
    if not inside:
        raise _TerminalInput("BAD_PATH",
                             f"source_uri di luar storage: {source_uri}")
    # Tolak symlink di sepanjang path (tanpa resolve — quirk Windows).
    rel = os.path.relpath(target, base)
    prefix = base
    for seg in rel.split(os.sep):
        prefix = os.path.join(prefix, seg)
        if os.path.islink(prefix):
            raise _TerminalInput("BAD_PATH",
                                 f"source_uri mengandung symlink: {source_uri}")
    try:
        return Path(target).read_bytes()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _TerminalInput("BAD_PATH", f"blob tak terbaca: {exc}") from exc


async def process_job(ctx: WorkerCtx, job: dict) -> str:
    """Jalankan 1 job sampai done/failed/dead. Return state akhir."""
    from app.infra import ingest_repo as repo

    settings, pool = ctx.settings, ctx.pool
    tenant, job_id = job["tenant"], job["id"]
    doc_id = str(job["document_id"])
    stop_hb = asyncio.Event()
    lease_lost = asyncio.Event()

    async def _heartbeat() -> None:
        misses = 0
        while not stop_hb.is_set():
            try:
                alive = await repo.heartbeat(
                    pool, job_id=job_id, worker_id=ctx.worker_id,
                    tenant=tenant,
                    lease_seconds=settings.RAG_LEASE_SECONDS)
                if not alive:
                    misses += 1
                    log.warning("job %s: lease hilang (%d/2)", job_id, misses)
                    if misses >= 2:
                        lease_lost.set()
                        return
                else:
                    misses = 0
            except Exception as exc:  # noqa: BLE001 - heartbeat best-effort
                log.debug("heartbeat job %s gagal: %s", job_id, exc)
            try:
                await asyncio.wait_for(stop_hb.wait(), HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                continue

    hb_task = asyncio.create_task(_heartbeat())
    stages = asyncio.create_task(_run_stages(ctx, job, tenant, job_id,
                                             doc_id))
    waiter = asyncio.create_task(lease_lost.wait())
    try:
        await asyncio.wait({stages, waiter},
                           return_when=asyncio.FIRST_COMPLETED)
        if lease_lost.is_set() and not stages.done():
            stages.cancel()  # lease dicuri: BERHENTI, jangan fail_job (K1)
            try:
                await stages
            except asyncio.CancelledError:
                pass
            log.warning("job %s dibatalkan: lease dicuri worker lain", job_id)
            return "stolen"
        return await stages
    finally:
        if not waiter.done():
            waiter.cancel()
        stop_hb.set()
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass


async def _run_stages(ctx: WorkerCtx, job: dict, tenant: str,
                      job_id: int, doc_id: str) -> str:
    from app.infra import ingest_repo as repo
    from app.rag import citations as C
    from app.rag.chunker import (
        chunk_factoid_lines,
        chunk_markdown,
        chunk_pages,
        chunk_text,
    )
    from app.rag.parsers.sniff import PDF, sniff_mime

    settings, pool = ctx.settings, ctx.pool

    async def _stage(stage: str, progress: int) -> None:
        # Koneksi pendek per update (T11): buka-tulis-tutup.
        await repo.set_stage(pool, job_id=job_id, stage=stage,
                             progress=progress, tenant=tenant)
        await repo.set_document_status(pool, tenant=tenant,
                                       document_id=doc_id, status=stage)
        await publish(ctx, tenant, job_id, "ingest_progress", stage, progress)

    try:
        doc = await repo.get_document(pool, tenant=tenant, document_id=doc_id)
        if doc is None or doc["status"] == "deleted":
            await repo.fail_job(pool, job_id=job_id, tenant=tenant,
                                document_id=doc_id,
                                error="DOCUMENT_GONE: dokumen dihapus saat antre",
                                retry_at=None, touch_document=False)
            return "dead"

        blob = _read_blob(settings, doc["source_uri"])
        mime = sniff_mime(blob, doc["title"])
        parser = ctx.parsers.get(mime)
        if parser is None:
            from app.core.errors import UnsupportedMediaError
            raise UnsupportedMediaError(f"sniff:{mime}")

        await _stage("parsing", 10)
        preset = doc["chunk_preset"] or "prose"
        ocr_flag = (doc.get("meta") or {}).get("ocr", settings.OCR_MODE)
        ocr_conf: float | None = None
        low_ocr = False

        if mime == PDF:
            from app.infra.rag.pdf_probe import probe_pdf

            probe = await probe_pdf(blob)
            if probe["pages"] > settings.RAG_MAX_PAGES_PDF:
                from app.core.errors import UnsupportedMediaError
                raise UnsupportedMediaError(
                    f"PDF {probe['pages']} halaman > batas "
                    f"{settings.RAG_MAX_PAGES_PDF}.")
            if probe["looks_scanned"] and ocr_flag != "on":
                raise _OcrRequired(
                    "Dokumen hasil pindai — ulangi retry dengan ocr=on.")
            if probe["looks_scanned"]:
                ocr = await ctx.ocr.ocr(
                    blob, langs=settings.OCR_LANGS,
                    timeout_seconds=settings.OCR_TIMEOUT_SECONDS,
                    max_pages=settings.OCR_MAX_PAGES)
                ocr_conf = float(ocr["confidence"])
                low_ocr = ocr_conf < OCR_CONF_THRESHOLD
                pages = [{"page": p["page"], "text": p["text"], "headings": []}
                         for p in ocr["pages"]]
                tables: list = []
                parsed_meta = {"ocr_applied": True,
                               "ocr_confidence": ocr_conf,
                               "low_ocr": low_ocr}
            else:
                parsed = await parser.parse(blob, mime=mime)
                pages, tables = parsed["pages"], parsed["tables"]
                parsed_meta = {"ocr_applied": False}
        else:
            parsed = await parser.parse(blob, mime=mime)
            pages, tables = parsed["pages"], parsed["tables"]
            parsed_meta = {"ocr_applied": False}

        await _stage("chunking", 40)
        # id_key: None = ord global (teks); "tbl:..." = namespace tabel yang
        # stabil terhadap penyisipan paragraf (F-5).
        chunks: list = []  # (content, page, sheet, heading, headers, id_key)
        if mime == PDF:
            # PDF SELALU per-halaman (fidelitas sitasi > preferensi preset, P7).
            for c in chunk_pages(
                    [{"page": p["page"], "text": p["text"]} for p in pages],
                    ctx.tok):
                chunks.append((c.content, c.page, None, None, [], None))
        elif preset == "markdown" or mime in ("text/markdown", "text/plain"):
            full = "\n\n".join(p["text"] for p in pages)
            if preset == "factoid":
                for c in chunk_factoid_lines(full.splitlines(), ctx.tok):
                    chunks.append((c.content, 1, None, None, [], None))
            else:
                for c in chunk_markdown(full, ctx.tok):
                    chunks.append((c.content, 1, None, c.heading, c.headers,
                                   None))
        else:
            full = "\n\n".join(p["text"] for p in pages)
            if preset == "factoid":
                for c in chunk_factoid_lines(full.splitlines(), ctx.tok):
                    chunks.append((c.content, 1, None, None, [], None))
            else:
                for c in chunk_text(full, preset, ctx.tok):
                    chunks.append((c.content, 1, None, None, [], None))
        # Baris tabel → kalimat faktual (selalu, semua preset; dibatasi 256
        # tok + baris kosong dilewati agar tak langgar CHECK chunks, P6).
        for t in tables:
            for row in t["rows"]:
                line = table_to_factoid(t.get("sheet"), t["headers"], row)
                if not line or not any((v or "").strip() for v in row):
                    continue
                if ctx.tok.count(line) > 256:
                    line = ctx.tok.truncate_words(line, 256)
                rowhash = hashlib.sha256(
                    "|".join(row).encode()).hexdigest()[:8]
                id_key = f"tbl:{t.get('sheet') or '-'}:{rowhash}"
                chunks.append((line, t.get("page"), t.get("sheet"),
                               None, [], id_key))
        if not chunks:
            await repo.fail_job(
                pool, job_id=job_id, tenant=tenant, document_id=doc_id,
                error="EMPTY_DOCUMENT: tak ada teks terekstrak", retry_at=None)
            return "dead"

        await _stage("embedding", 60)
        # HTTP murni — TANPA koneksi DB terbuka (T11).
        texts = [c[0] for c in chunks]
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            batch = await ctx.embed.embed_texts(texts[i:i + EMBED_BATCH])
            vectors.extend(normalize_vector(v) for v in batch)

        await _stage("indexing", 85)
        from app.rag.chunker import CHUNKER_VERSION

        id_version = f"{CHUNKER_VERSION}:{preset}"
        doc_meta = doc.get("meta") or {}
        rows = []
        for seq, ((content, page, sheet, heading, headers, id_key),
                  vec) in enumerate(zip(chunks, vectors)):
            meta = {"source_type": doc["source_type"], "lang": doc["lang"],
                    "headers": headers,
                    "_chunk_id": C.chunk_id(doc_id,
                                            id_key if id_key else seq,
                                            id_version)}
            if ocr_conf is not None:
                meta["ocr_confidence"] = ocr_conf
                meta["low_ocr"] = low_ocr
            for key in ("doc_type", "access_level"):
                if key in doc_meta:
                    meta[key] = doc_meta[key]
            rows.append({"content": content, "vector": vec, "ord": seq,
                         "page": page, "sheet": sheet, "heading": heading,
                         "token_count": ctx.tok.count(content), "metadata": meta})
        from app.infra.rag.pgvector import PgVectorStore

        dims = getattr(ctx.embed, "dims", 0) or len(vectors[0])
        pg = PgVectorStore(pool, tenant=tenant,
                           model=settings.RAG_EMBED_MODEL,
                           dim=dims,
                           version=getattr(ctx.embed, "version", "v1"))
        # Hapus index lama DULU dalam job yang sama (retry/re-ingest tak
        # sisakan ord basi, K9). Idempotent: retry = hasil akhir sama.
        await pg.delete_document(doc_id=doc_id)
        await pg.upsert_chunks(doc_id=doc_id, chunks=rows)
        if settings.RAG_BACKEND == "qdrant" and ctx.qdrant is not None:
            from app.infra.rag.qdrant import QdrantStore, collection_name

            q = QdrantStore(
                ctx.qdrant,
                collection_name(dims, settings.RAG_EMBED_MODEL),
                tenant=tenant, model=settings.RAG_EMBED_MODEL,
                dim=dims,
                version=getattr(ctx.embed, "version", "v1"))
            await q.ensure_collection()  # idempotent (K8)
            await q.delete_document(doc_id=doc_id)
            for r in rows:  # source ikut payload (tanpa join, D4)
                r["metadata"]["_source"] = doc["title"]
            await q.upsert_chunks(doc_id=doc_id, chunks=rows)

        finished = await repo.finish_job(pool, job_id=job_id, tenant=tenant,
                                         document_id=doc_id)
        if not finished:
            # Kalah race dengan delete (K4): chunk sudah terlanjur masuk —
            # bersihkan agar hapus utuh.
            await pg.delete_document(doc_id=doc_id)
            if settings.RAG_BACKEND == "qdrant" and ctx.qdrant is not None:
                from app.infra.rag.qdrant import QdrantStore, collection_name

                q2 = QdrantStore(
                    ctx.qdrant,
                    collection_name(dims, settings.RAG_EMBED_MODEL),
                    tenant=tenant)
                await q2.delete_document(doc_id=doc_id)
            log.info("job %s: dokumen dihapus saat indexing → dibersihkan",
                     job_id)
            return "dead"
        await repo.set_document_status(
            pool, tenant=tenant, document_id=doc_id, status="ready",
            meta_patch={"chunks": len(rows), **parsed_meta})
        await publish(ctx, tenant, job_id, "ingest_result", "done", 100)
        log.info("job %s done (%s, %d chunk)", job_id, doc_id, len(rows))
        return "done"
    except _OcrRequired as exc:
        from app.infra import ingest_repo as repo

        await repo.fail_job(pool, job_id=job_id, tenant=tenant,
                            document_id=doc_id,
                            error=f"OCR_REQUIRED: {exc}", retry_at=None)
        await repo.set_document_status(
            pool, tenant=tenant, document_id=doc_id, status="failed",
            meta_patch={"ocr_required": True})
        await publish(ctx, tenant, job_id, "ingest_error", "dead", 0)
        return "dead"
    except Exception as exc:  # noqa: BLE001 - klasifikasikan semua
        from app.infra import ingest_repo as repo

        # attempts sudah +1 saat klaim → backoff pakai attempts-1 (P2-20).
        attempt_no = max(0, job.get("attempts", 0) - 1)
        retry, code, msg = classify_error(exc, job.get("attempts", 0))
        retry_at = (datetime.now(timezone.utc) + timedelta(
            seconds=compute_backoff(attempt_no))) if retry else None
        state = await repo.fail_job(
            pool, job_id=job_id, tenant=tenant, document_id=doc_id,
            error=f"{code}: {msg}", retry_at=retry_at)
        if state == "failed":
            # Status dokumen konsisten saat retryable (P10).
            await repo.set_document_status(
                pool, tenant=tenant, document_id=doc_id, status="pending",
                meta_patch={"retry_at": retry_at.isoformat(),
                            "last_error": f"{code}: {msg}"[:300]})
        await publish(ctx, tenant, job_id, "ingest_error", state, 0)
        log.warning("job %s → %s (%s)", job_id, state, code)
        return state


# ---------------------------------------------------------------- poll loop

async def run_worker(settings, pool, *, worker_id: str,
                     max_jobs: int | None = None,
                     stop: asyncio.Event | None = None,
                     ctx: WorkerCtx | None = None) -> int:
    """Loop klaim-kerja. `max_jobs` = hook test. Return jumlah job selesai."""
    from app.infra import ingest_repo as repo

    context = ctx or await build_ctx(settings, pool, worker_id=worker_id)
    stop = stop or asyncio.Event()
    sem = asyncio.Semaphore(max(1, settings.WORKER_CONCURRENCY))
    done = 0
    in_flight = 0

    async def _one(job: dict) -> None:
        nonlocal done, in_flight
        in_flight += 1
        try:
            async with sem:
                try:
                    await process_job(context, job)
                except Exception:  # noqa: BLE001 - tak boleh bocor
                    log.exception("job %s bocor exception", job.get("id"))
        finally:
            in_flight -= 1
            done += 1

    while not stop.is_set():
        # Hook test: JANGAN over-klaim (in_flight + done dibatasi, minor).
        if max_jobs is not None and done + in_flight >= max_jobs:
            if in_flight == 0:
                break
            await asyncio.sleep(0.1)
            continue
        try:
            job = await repo.claim_job(
                pool, worker_id=worker_id,
                lease_seconds=settings.RAG_LEASE_SECONDS)
        except Exception as exc:
            log.warning("claim gagal (%s) — tidur %ss", type(exc).__name__,
                        settings.WORKER_POLL_SECONDS)
            await asyncio.sleep(settings.WORKER_POLL_SECONDS)
            continue
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(),
                                       settings.WORKER_POLL_SECONDS)
            except asyncio.TimeoutError:
                continue
            break
        await publish(context, job["tenant"], job["id"],
                      "ingest_started", "leased", 0)
        task = asyncio.create_task(_one(job))
        if max_jobs is not None and settings.WORKER_CONCURRENCY == 1:
            await task  # mode test: serial deterministik
    return done


async def release_all(pool, *, worker_id: str) -> int:
    """Lepas semua lease milik worker ini (shutdown graceful).

    Semua non-terminal (009: state tetap leased saat kerja; baris transition
    ikut ter-cover). Tanpa SET LOCAL: klaim worker lintas-tenant by design
    (fungsi klaim privileged; release milik sendiri via claimed_by).
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE ingestion_jobs SET lease_until = now(), claimed_by = NULL,"
            " updated_at = now() WHERE claimed_by = %s"
            " AND state NOT IN ('done', 'failed', 'dead', 'queued')",
            [worker_id])
        return cur.rowcount or 0


def main() -> None:
    """Entrypoint `python -m app.workers.ingest`."""
    # Env HF DULU (sebelum import docling/transformers), hormati operator.
    from app.core.config import get_settings
    from app.infra.lifespan import ensure_selector_loop

    ensure_selector_loop()
    settings = get_settings()
    os.environ.setdefault("HF_HUB_CACHE",
                          str(Path(settings.RAG_HF_CACHE).resolve()))
    os.environ.setdefault("HF_HOME",
                          str(Path(settings.RAG_HF_CACHE).resolve().parent))

    from app.infra.postgres import close_pool, create_pool, open_pool

    async def _amain() -> None:
        pool = create_pool(
            settings, min_size=settings.WORKER_DB_POOL_MIN,
            max_size=settings.WORKER_DB_POOL_MAX,
            statement_timeout=settings.WORKER_STATEMENT_TIMEOUT_S,
            idle_txn_timeout=settings.DB_IDLE_TXN_TIMEOUT_S)
        try:
            await open_pool(pool)
        except Exception as exc:
            raise SystemExit(f"Worker: pool gagal dibuka ({exc})") from exc
        worker_id = f"{socket.gethostname()}-{os.getpid()}"
        log.info("worker %s start (concurrency=%d, backend=%s)", worker_id,
                 settings.WORKER_CONCURRENCY, settings.RAG_BACKEND)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass  # Windows: SIGTERM tak didukung penuh
        try:
            await run_worker(settings, pool, worker_id=worker_id, stop=stop)
        finally:
            n = await release_all(pool, worker_id=worker_id)
            log.info("worker %s stop (lease dilepas: %d)", worker_id, n)
            await close_pool(pool)

    asyncio.run(_amain())


if __name__ == "__main__":
    main()
