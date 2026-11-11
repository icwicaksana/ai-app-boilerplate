"""Documents di level HTTP via TestClient (fake controller, tanpa DB).

Mengunci: validasi upload, bentuk respons, 404/409, frame SSE, idempotency.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import dependencies as deps
from app.core.errors import PayloadTooLargeError, UnsupportedMediaError
from tests._auth_helpers import app_with_settings, auth_headers

DOC = {
    "id": "d" * 32, "title": "a.md", "source_type": "md", "status": "ready",
    "chunk_preset": "markdown", "bytes": 10, "created_at": datetime(
        2026, 1, 1, tzinfo=timezone.utc),
}
JOB_DONE = {"id": 7, "document_id": DOC["id"], "state": "done",
            "stage": "done", "progress": 100, "attempts": 1,
            "last_error": None}
JOB_RUN = {"id": 8, "document_id": DOC["id"], "state": "leased",
           "stage": "embedding", "progress": 60, "attempts": 1,
           "last_error": None}


class _FakeCtrl:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    async def enqueue(self, **kw):
        if kw.get("_raise"):
            raise kw["_raise"]
        return {"document_id": DOC["id"], "job_id": 7, "deduped": False}

    async def get(self, **kw):
        return self._detail

    async def list(self, **kw):
        return [DOC], 1

    async def delete(self, **kw):
        return self._deleted

    async def job_status(self, **kw):
        return self._job

    async def retry(self, **kw):
        return self._retried

    async def search(self, **kw):
        return {"matches": [{"chunk_id": "c1", "doc_id": DOC["id"],
                             "content": "x", "source": "a.md", "page": 1,
                             "score": 0.9, "backend": "pgvector",
                             "metadata": {}}],
                "status": "ok", "degraded": [], "backend": "pgvector"}


class _FakeCache:
    """Idempotency store in-memory (set_if_absent/get_json/set_json/delete)."""

    def __init__(self):
        self.d = {}

    async def incr_window(self, key, ttl_seconds):
        return 1, 60  # selalu di bawah batas

    async def exists(self, key):
        return False  # denylist JWT kosong

    async def set_if_absent(self, key, value, ttl):
        import json as _json

        if key in self.d:
            return False
        self.d[key] = _json.loads(value)
        return True

    async def get_json(self, key):
        return self.d.get(key)

    async def set_json(self, key, value, ttl_seconds):
        self.d[key] = value

    async def delete(self, key):
        self.d.pop(key, None)


def _client(ctrl, cache=None):
    app = app_with_settings()
    app.dependency_overrides[deps.get_document_controller] = lambda: ctrl
    app.dependency_overrides[deps.get_cache] = lambda: cache
    app.dependency_overrides[deps.get_rag_clients] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


def _hdr(**kw):
    args = {"tenant": "toko-a", "user": "budi", "request_id": "t1"}
    args.update(kw)
    return auth_headers(**args)


def _ctrl(**kw):
    base = {"_detail": {"document": DOC, "job": JOB_DONE, "chunks": 3},
            "_deleted": True, "_job": JOB_DONE, "_retried": {"id": 8}}
    base.update(kw)
    return _FakeCtrl(**base)


# --- upload ---

def test_upload_202_dan_bentuk():
    c = _client(_ctrl())
    r = c.post("/api/v1/documents", files={"file": ("a.md", b"# Halo\nisi",
                                                   "text/markdown")},
               data={"preset": "markdown"}, headers=_hdr())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["document_id"] == DOC["id"] and body["job_id"] == 7
    assert body["deduped"] is False


def test_upload_preset_salah_422():
    c = _client(_ctrl())
    r = c.post("/api/v1/documents", files={"file": ("a.md", b"x")},
               data={"preset": "nano"}, headers=_hdr())
    assert r.status_code == 422


def test_upload_idempotency_replay_dan_konflik():
    cache = _FakeCache()
    c = _client(_ctrl(), cache=cache)
    files = {"file": ("a.md", b"# Halo", "text/markdown")}
    h1 = {**_hdr(), "Idempotency-Key": "k1"}
    r1 = c.post("/api/v1/documents", files=files, headers=h1)
    assert r1.status_code == 202
    r2 = c.post("/api/v1/documents", files=files, headers=h1)
    assert r2.status_code == 202
    assert r2.headers.get("Idempotent-Replayed") == "true"
    r3 = c.post("/api/v1/documents",
                files={"file": ("b.md", b"beda", "text/markdown")}, headers=h1)
    assert r3.status_code == 409


# --- baca/hapus ---

def test_get_detail_bentuk():
    c = _client(_ctrl())
    r = c.get(f"/api/v1/documents/{DOC['id']}", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["chunks"] == 3
    assert body["job_id"] == 7


def test_get_asing_404():
    c = _client(_ctrl(_detail=None))
    r = c.get("/api/v1/documents/tak-ada", headers=_hdr())
    assert r.status_code == 404
    assert r.json()["code"] == "DOCUMENT_NOT_FOUND"


def test_list_bentuk_meta():
    c = _client(_ctrl())
    r = c.get("/api/v1/documents?limit=5", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["meta"] == {"total": 1, "limit": 20, "offset": 0} or \
        body["meta"]["total"] == 1
    assert body["items"][0]["document_id"] == DOC["id"]


def test_list_status_asing_422():
    c = _client(_ctrl())
    r = c.get("/api/v1/documents?status=ngawur", headers=_hdr())
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_STATUS"


def test_delete_202_dan_404():
    assert _client(_ctrl()).delete(
        f"/api/v1/documents/{DOC['id']}", headers=_hdr()).status_code == 202
    r = _client(_ctrl(_deleted=False)).delete(
        "/api/v1/documents/x", headers=_hdr())
    assert r.status_code == 404


# --- job ---

def test_job_status_bentuk_dan_gone():
    c = _client(_ctrl())
    r = c.get("/api/v1/ingestion_jobs/7", headers=_hdr())
    assert r.status_code == 200 and r.json()["state"] == "done"
    r2 = _client(_ctrl(_job=None)).get("/api/v1/ingestion_jobs/9",
                                       headers=_hdr())
    assert r2.status_code == 410


def test_retry_202_dan_409():
    c = _client(_ctrl())
    assert c.post("/api/v1/ingestion_jobs/8/retry", json={},
                  headers=_hdr()).status_code == 202
    c2 = _client(_ctrl(_retried=None, _job=JOB_RUN))
    r = c2.post("/api/v1/ingestion_jobs/8/retry", json={}, headers=_hdr())
    assert r.status_code == 409


# --- SSE ---

def test_sse_hasil_langsung_untuk_done():
    c = _client(_ctrl())
    with c.stream("GET", f"/api/v1/documents/{DOC['id']}/progress",
                  headers=_hdr()) as r:
        assert r.status_code == 200
        text = r.read().decode()
    assert "event: ingest_started" in text
    assert "event: ingest_result" in text
    assert '"chunks": 3' in text


def test_sse_error_untuk_failed():
    job = {**JOB_DONE, "state": "dead", "last_error": "OCR_REQUIRED: x"}
    c = _client(_ctrl(_job=job))
    with c.stream("GET", f"/api/v1/documents/{DOC['id']}/progress",
                  headers=_hdr()) as r:
        text = r.read().decode()
    assert "event: ingest_error" in text
    assert "OCR_REQUIRED" in text


def test_sse_failed_retry_lanjut_bukan_terminal():
    """`failed` (terjadwal retry) → retry_scheduled + poll lanjut (P0-3)."""

    class _Scripted(_FakeCtrl):
        def __init__(self, script):
            super().__init__(
                _detail={"document": DOC, "job": script[0], "chunks": 3},
                _deleted=True, _job=script[0], _retried=None)
            self._script = script
            self._n = 0

        async def job_status(self, **kw):
            out = self._script[min(self._n, len(self._script) - 1)]
            self._n += 1
            return out

    failed = {**JOB_DONE, "state": "failed", "stage": "embedding",
              "progress": 60, "last_error": "EMBED_TIMEOUT: x"}
    c = _client(_Scripted([failed, failed, JOB_DONE]))
    with c.stream("GET", f"/api/v1/documents/{DOC['id']}/progress",
                  headers=_hdr()) as r:
        text = r.read().decode()
    assert "retry_scheduled" in text
    assert "event: ingest_result" in text  # lanjut sampai done


def test_sse_404_dokumen_asing():
    c = _client(_ctrl(_detail=None))
    r = c.get("/api/v1/documents/x/progress", headers=_hdr())
    assert r.status_code == 404


# --- search ---

def test_search_bentuk_sitasi():
    c = _client(_ctrl())
    r = c.post("/api/v1/retrieval/search", json={"query": "retur?", "k": 5},
               headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["matches"][0]["n"] == 1
    assert body["meta"]["backend"] == "pgvector"


def test_search_validasi():
    c = _client(_ctrl())
    assert c.post("/api/v1/retrieval/search", json={"query": ""},
                  headers=_hdr()).status_code == 422
    assert c.post("/api/v1/retrieval/search", json={"query": "x", "k": 99},
                  headers=_hdr()).status_code == 422


# --- validasi controller (tanpa DB: raise sebelum query) ---

@pytest.mark.asyncio
async def test_enqueue_tolak_sebelum_db():
    from app.controllers.DocumentController import DocumentController
    from app.core.config import Settings

    s = Settings(_env_file=None, DATABASE_URL="x", PHOENIX_ENABLED=False)
    ctrl = DocumentController(s, pool=None)
    with pytest.raises(PayloadTooLargeError):
        await ctrl.enqueue(tenant="t", user="u", filename="a.pdf",
                           blob=b"x" * (s.RAG_MAX_UPLOAD_BYTES + 1),
                           preset="prose", ocr="auto")
    with pytest.raises(UnsupportedMediaError):
        await ctrl.enqueue(tenant="t", user="u", filename="a.bin",
                           blob=b"\x89PNG....", preset="prose", ocr="auto")
    with pytest.raises(UnsupportedMediaError):
        await ctrl.enqueue(tenant="t", user="u", filename="a.xlsm",
                           blob=b"PK\x03\x04xx", preset="prose", ocr="auto")
    with pytest.raises(UnsupportedMediaError):
        await ctrl.enqueue(tenant="t", user="u", filename="a.md",
                           blob=b"", preset="prose", ocr="auto")
