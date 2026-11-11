"""Live API dokumen: lifespan nyata + Postgres + Valkey + Infinity.

Siklus: upload → proses inline (repo+worker) → GET/SSE/search → retry → hapus.
Klien module-scoped (lifespan RAG ±20 dtk, sekali saja); tenant unik per test.
"""

import asyncio
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import hash_password
from app.infra import users
from app.main import create_app

pytestmark = pytest.mark.integration

SECRET = "integration-secret-" + "z" * 32
MD = ("## Kebijakan Retur\n\nBarang bisa diretur maksimal 14 hari "
      "dengan nota.\n").encode()


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """Lifespan nyata sekali per modul. Env dibaca inline (fixture require_*
    function-scoped tak bisa dipakai dari module-scope)."""
    import os
    import urllib.request

    db_url = (os.environ.get("TEST_DATABASE_URL")
              or os.environ.get("DATABASE_URL", "")).strip()
    cache_url = (os.environ.get("TEST_CACHE_URL")
                 or os.environ.get("CACHE_URL", "")).strip()
    embed_url = (os.environ.get("TEST_EMBED_BASE_URL")
                 or os.environ.get("RAG_EMBED_BASE_URL",
                                   "http://localhost:7997")).strip()
    if not db_url or not cache_url:
        pytest.skip("DATABASE_URL/CACHE_URL kosong — nyalakan infra dulu")
    try:
        import psycopg

        from app.infra.postgres import normalize_conninfo

        psycopg.connect(normalize_conninfo(db_url),
                        connect_timeout=2).close()
        urllib.request.urlopen(embed_url.rstrip("/") + "/health", timeout=3)
    except Exception as exc:
        pytest.skip(f"infra tak terjangkau ({type(exc).__name__})")
    storage = tmp_path_factory.mktemp("uploads")
    settings = Settings(
        _env_file=None, DATABASE_URL=db_url, CACHE_URL=cache_url,
        AUTH_MODE="jwt", JWT_SECRET=SECRET, ENVIRONMENT="dev",
        RATE_LIMIT_ENABLED=False, ENABLE_DOCS=False, PHOENIX_ENABLED=False,
        RAG_EMBED_BASE_URL=embed_url, RAG_STORAGE_DIR=str(storage))
    with TestClient(create_app(settings)) as client:
        yield {"client": client, "settings": settings,
               "db_url": db_url, "embed_url": embed_url}


def _seed_user(db_url, tenant, username, password):
    async def _go():
        from app.infra.postgres import close_pool, create_pool, open_pool

        pool = create_pool(Settings(_env_file=None, DATABASE_URL=db_url))
        await open_pool(pool)
        try:
            await users.ensure_schema(pool)
            await users.create_user(
                pool, tenant=tenant, username=username,
                password_hash=hash_password(password))
        finally:
            await close_pool(pool)

    asyncio.run(_go())


@pytest.fixture
def auth(live):
    db_url = live["db_url"]
    tenant = "it-doc-" + uuid.uuid4().hex[:6]
    username = "u" + uuid.uuid4().hex[:6]
    password = "rahasia-" + uuid.uuid4().hex[:8]
    _seed_user(db_url, tenant, username, password)
    res = live["client"].post("/api/v1/auth/login", json={
        "tenant": tenant, "username": username, "password": password})
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return tenant, {"Authorization": f"Bearer {token}"}


def _process_inline(db_url, embed_url, storage, tenant, document_id):
    """Jalankan worker inline (pool+embed sendiri, loop sendiri)."""
    from app.infra import ingest_repo as repo
    from app.infra.postgres import close_pool, create_pool, open_pool
    from app.infra.rag.infinity import InfinityEmbeddings
    from app.infra.rag.tokenizer import BgeTokenizer
    from app.workers.ingest import WorkerCtx, build_parsers, process_job

    async def _go():
        settings = Settings(
            _env_file=None, DATABASE_URL=db_url, DB_POOL_MIN=1,
            DB_POOL_MAX=5, RAG_STORAGE_DIR=str(storage))
        pool = create_pool(settings)
        await open_pool(pool)
        embed = InfinityEmbeddings(embed_url, "BAAI/bge-m3", 1024,
                                   timeout_ms=120_000)
        try:
            # Purge residu run lain (queue shared, tmp per-run).
            async with pool.connection() as _c:
                await _c.execute(
                    "DELETE FROM documents WHERE tenant LIKE 'it-doc-%%'"
                    " AND id::text != %s", (document_id,))
            job = await repo.latest_job_for_document(
                pool, tenant=tenant, document_id=document_id)
            assert job is not None
            claimed = await repo.claim_job(pool, worker_id="live-inline")
            assert claimed is not None and claimed["id"] == job["id"], \
                "queue tercemar job basi"
            parsers, ocr = build_parsers(settings)
            ctx = WorkerCtx(
                settings=settings, pool=pool, worker_id="live-inline",
                embed=embed, tok=BgeTokenizer(cache_dir=".hf-cache"),
                qdrant=None, cache=None, parsers=parsers, ocr=ocr)
            return await process_job(ctx, claimed)
        finally:
            await embed.aclose()
            await close_pool(pool)

    return asyncio.run(_go())


def test_live_upload_proses_search_hapus(live, auth):
    client = live["client"]
    tenant, headers = auth
    storage = live["settings"].RAG_STORAGE_DIR

    up = client.post("/api/v1/documents",
                     files={"file": ("retur.md", MD, "text/markdown")},
                     data={"preset": "markdown"}, headers=headers)
    assert up.status_code == 202, up.text
    doc_id, job_id = up.json()["document_id"], up.json()["job_id"]

    got = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert got.status_code == 200 and got.json()["status"] == "pending"

    assert _process_inline(live["db_url"], live["embed_url"], storage,
                           tenant, doc_id) == "done"

    got2 = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert got2.json()["status"] == "ready", got2.text
    assert got2.json()["chunks"] >= 1

    sse = client.get(f"/api/v1/documents/{doc_id}/progress", headers=headers)
    assert sse.status_code == 200
    assert "event: ingest_result" in sse.text

    search = client.post("/api/v1/retrieval/search",
                         json={"query": "berapa lama retur?", "k": 3},
                         headers=headers)
    assert search.status_code == 200, search.text
    # Bentuk + tenant-bound (kualitas ranking = eval mini F6, bukan di sini).
    assert search.json()["matches"], search.text
    assert search.json()["matches"][0]["n"] == 1
    assert search.json()["meta"]["backend"] == "pgvector"

    rm = client.delete(f"/api/v1/documents/{doc_id}", headers=headers)
    assert rm.status_code == 202
    got3 = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert got3.json()["status"] == "deleted"
    assert got3.json()["chunks"] == 0


def test_live_idempotency_key_asli(live, auth):
    client = live["client"]
    _, headers = auth
    h = {**headers, "Idempotency-Key": "live-" + uuid.uuid4().hex[:8]}
    files = {"file": ("a.md", b"# Halo", "text/markdown")}
    r1 = client.post("/api/v1/documents", files=files, headers=h)
    assert r1.status_code == 202
    r2 = client.post("/api/v1/documents", files=files, headers=h)
    assert r2.status_code == 202
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert r2.json()["document_id"] == r1.json()["document_id"]


def test_live_lintas_tenant_404(live, auth):
    client = live["client"]
    tenant_a, headers_a = auth
    up = client.post("/api/v1/documents",
                     files={"file": ("rahasia.md", b"rahasia A",
                                     "text/markdown")},
                     headers=headers_a)
    doc_id = up.json()["document_id"]

    tenant_b = "it-doc-" + uuid.uuid4().hex[:6]
    user_b, pass_b = "ub" + tenant_b[-4:], "p-" + uuid.uuid4().hex[:8]
    _seed_user(live["db_url"], tenant_b, user_b, pass_b)
    login = client.post("/api/v1/auth/login", json={
        "tenant": tenant_b, "username": user_b, "password": pass_b})
    headers_b = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.get(f"/api/v1/documents/{doc_id}",
                      headers=headers_b).status_code == 404
    assert client.delete(f"/api/v1/documents/{doc_id}",
                         headers=headers_b).status_code == 404
    assert client.get("/api/v1/documents",
                      headers=headers_b).json()["meta"]["total"] == 0


def test_live_retry_kosong_dead_lalu_ocr_flag(live, auth):
    client = live["client"]
    tenant, headers = auth
    up = client.post("/api/v1/documents",
                     files={"file": ("kosong.md", b"\n\n",
                                     "text/markdown")},
                     headers=headers)
    doc_id, job_id = up.json()["document_id"], up.json()["job_id"]
    assert _process_inline(live["db_url"], live["settings"].RAG_EMBED_BASE_URL,
                           live["settings"].RAG_STORAGE_DIR, tenant,
                           doc_id) == "dead"
    job = client.get(f"/api/v1/ingestion_jobs/{job_id}", headers=headers)
    assert "EMPTY_DOCUMENT" in (job.json()["last_error"] or "")
    retry = client.post(f"/api/v1/ingestion_jobs/{job_id}/retry",
                        json={"ocr": "on"}, headers=headers)
    assert retry.status_code == 202
    job2 = client.get(f"/api/v1/ingestion_jobs/{job_id}", headers=headers)
    assert job2.json()["state"] == "queued"
    # Retry saat queued → 409 (bukan antre ganda).
    retry2 = client.post(f"/api/v1/ingestion_jobs/{job_id}/retry", json={},
                         headers=headers)
    assert retry2.status_code == 409
