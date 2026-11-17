"""v1.0 V1-D/2: clearance JWT → filter wajib (G6, fail-closed)."""

from __future__ import annotations

import pytest

from app.core.acl import allowed_levels, may_read, normalize_level


def test_acl_hierarki_dan_fail_closed():
    assert normalize_level("INTERNAL") == "internal"
    assert normalize_level("ngawur") == "public"
    assert normalize_level(None) == "public"
    assert allowed_levels("public") == ["public"]
    assert allowed_levels("internal") == ["public", "internal"]
    assert allowed_levels("restricted") == ["public", "internal", "restricted"]
    assert allowed_levels("ngawur") == ["public"]
    assert may_read("internal", "public") and may_read("internal", "internal")
    assert not may_read("internal", "restricted")
    assert not may_read("public", "internal")


def test_jwt_clearance_roundtrip_dan_legacy():
    from app.core.security import create_access_token, decode_token
    from tests._auth_helpers import test_settings

    s = test_settings()
    token, claims = create_access_token(s, workspace="toko-a", user="budi",
                                        clearance="internal")
    assert claims.clearance == "internal"
    back = decode_token(s, token)
    assert back.clearance == "internal"
    # Token pra-G6 tanpa klaim → public (kompatibel, fail-closed).
    import jwt as _jwt

    legacy = _jwt.encode(
        {"sub": "budi", "workspace": "toko-a", "typ": "access",
         "jti": "x" * 32, "iss": s.JWT_ISSUER, "iat": 1_700_000_000,
         "exp": 9_999_999_999},
        s.JWT_SECRET, algorithm="HS256")
    assert decode_token(s, legacy).clearance == "public"


def test_identity_bawa_clearance():
    from app.core.auth import _claims_to_identity
    from app.core.security import TokenClaims

    ident = _claims_to_identity(TokenClaims(
        subject="budi", workspace="toko-a", jti="j", token_type="access",
        clearance="restricted"))
    assert (ident.workspace, ident.user, ident.clearance) == (
        "toko-a", "budi", "restricted")


def test_merge_intersect_tak_bisa_melebar():
    from app.rag.pipeline import HybridRetriever

    r = HybridRetriever.__new__(HybridRetriever)
    r._default_filters = {"access_level": ["public", "internal"]}
    # Panggilan minta restricted → intersect buang (fail-closed).
    out = r._merge_filters({"access_level": ["restricted", "public"]})
    assert out == {"access_level": ["public"]}
    # Tanpa filter panggilan → default penuh.
    out = r._merge_filters(None)
    assert out == {"access_level": ["public", "internal"]}
    # Kunci biasa lewat; skalar dinormalisasi ke list.
    out = r._merge_filters({"access_level": "internal", "doc_type": "sop"})
    assert out == {"access_level": ["internal"], "doc_type": "sop"}


def test_pgvector_dan_graph_in_list():
    from app.infra.rag.pg_graph import PgGraphStore
    from app.infra.rag.pgvector import PgVectorStore

    clause, params = PgVectorStore._filter_clause(
        None, {"access_level": ["public", "internal"]})
    assert "= ANY(%s)" in clause and params == [["public", "internal"]]
    assert "COALESCE" in clause  # chunk lama tanpa kunci = public
    store = PgGraphStore(pool=None, workspace="toko-a")
    clause, params = store._access_clause({"access_level": ["public"]})
    assert clause == " AND e.access_level = ANY(%s)"
    assert params == [["public"]]


def test_qdrant_match_any():
    from app.infra.rag.qdrant import QdrantStore

    store = QdrantStore.__new__(QdrantStore)
    store._workspace = "toko-a"
    filt = store._base_filter({"access_level": ["public", "internal"]})
    acl = [c for c in filt.must if c.key == "meta.access_level"]
    assert len(acl) == 1
    assert sorted(acl[0].match.any) == ["internal", "public"]


@pytest.mark.asyncio
async def test_pipeline_dua_cabang_dan_graph_dapat_filter():
    """Dense + lexical + graph SEMUA terima filter clearance (G6)."""
    from app.rag.pipeline import HybridRetriever

    seen: dict = {}

    class _Store:
        backend_name = "pgvector"

        async def search_dense(self, qv, k=50, filters=None):
            seen["dense"] = filters
            return []

        async def search_lexical(self, q, k=50, filters=None, raw_query=""):
            seen["lex"] = filters
            return []

    class _Graph:
        backend_name = "pggraph"

        async def resolve_seeds(self, seeds):
            return list(seeds)

        async def list_aliases(self):
            return {}

        async def expand(self, seeds, *, k, filters=None, max_hops=2,
                         fanout=50):
            seen["graph"] = filters
            return []

    class _Embed:
        async def embed_texts(self, texts):
            return [[0.1] for _ in texts]

    class _Rerank:
        async def rerank(self, q, docs, top_n=None):
            return []

    class _Tok:
        def count(self, t):
            return 1

        def truncate_words(self, t, n):
            return t

    r = HybridRetriever(embed=_Embed(), rerank=_Rerank(), store=_Store(),
                        tok=_Tok(), graph=_Graph(),
                        default_filters={"access_level": ["public"]})
    await r.search("apa?")
    assert seen["dense"] == {"access_level": ["public"]}
    assert seen["lex"] == {"access_level": ["public"]}
    assert seen["graph"] == {"access_level": ["public"]}


def test_upload_lebih_tinggi_dari_clearance_403():
    """Public tak bisa mengunggah internal (write-up ditolak)."""
    from fastapi.testclient import TestClient

    from app.core.dependencies import get_chat_controller  # noqa: F401
    from app.core.dependencies import get_document_controller
    from tests._auth_helpers import app_with_settings, auth_headers

    class _Ctrl:
        async def enqueue(self, **kw):
            raise AssertionError("tak boleh sampai controller")

    app = app_with_settings()
    app.dependency_overrides[get_document_controller] = lambda: _Ctrl()
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post(
        "/api/v1/documents", headers=auth_headers(clearance="public"),
        files={"file": ("a.md", b"# halo", "text/markdown")},
        data={"access_level": "internal"})
    assert res.status_code == 403


def test_upload_internal_terus_ke_controller():
    from fastapi.testclient import TestClient

    from app.core.dependencies import get_document_controller
    from tests._auth_helpers import app_with_settings, auth_headers

    seen: dict = {}

    class _Ctrl:
        async def enqueue(self, **kw):
            seen.update(kw)
            return {"document_id": "d1", "job_id": 7, "deduped": False}

    app = app_with_settings()
    app.state.cache = None
    app.dependency_overrides[get_document_controller] = lambda: _Ctrl()
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post(
        "/api/v1/documents", headers=auth_headers(clearance="internal"),
        files={"file": ("a.md", b"# halo", "text/markdown")},
        data={"access_level": "internal"})
    assert res.status_code == 202
    assert seen["access_level"] == "internal"
