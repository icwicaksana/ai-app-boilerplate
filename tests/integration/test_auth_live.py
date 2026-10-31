"""Integrasi: auth end-to-end dengan Postgres + Valkey ASLI, lifespan nyata.

Membuktikan alur produksi: user di DB → login → Bearer → route terlindungi
→ refresh rotasi → logout revoke. Tanpa LLM (tak butuh proxy).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import hash_password
from app.infra import users
from app.main import create_app

pytestmark = pytest.mark.integration

SECRET = "integration-secret-" + "z" * 32


@pytest.fixture
def client(db_url, cache_url):
    settings = Settings(
        _env_file=None, DATABASE_URL=db_url, CACHE_URL=cache_url,
        AUTH_MODE="jwt", JWT_SECRET=SECRET, ENVIRONMENT="dev",
        RATE_LIMIT_ENABLED=False, ENABLE_DOCS=False, PHOENIX_ENABLED=False,
    )
    with TestClient(create_app(settings)) as c:  # jalankan lifespan nyata
        yield c


@pytest.fixture
def creds(client):
    """User unik per test (idempotent, tanpa bentrok)."""
    tenant = "it-" + uuid.uuid4().hex[:6]
    username = "u" + uuid.uuid4().hex[:6]
    password = "rahasia-" + uuid.uuid4().hex[:8]
    return tenant, username, password


async def _seed(db_url: str, tenant: str, username: str, password: str) -> None:
    """Seed user lewat pool SENDIRI (mirip CLI admin).

    Penting: JANGAN memakai `client.app.state.pool` — pool itu terikat ke
    event loop lifespan TestClient, sedangkan `asyncio.run()` membuat loop
    baru (psycopg async terikat loop → hang).
    """
    from app.infra.postgres import close_pool, create_pool, open_pool

    pool = create_pool(Settings(_env_file=None, DATABASE_URL=db_url))
    await open_pool(pool)
    try:
        await users.ensure_schema(pool)
        await users.create_user(pool, tenant=tenant, username=username,
                                password_hash=hash_password(password))
    finally:
        await close_pool(pool)


def _login(client, tenant, username, password):
    return client.post("/api/v1/auth/login",
                       json={"tenant": tenant, "username": username,
                             "password": password})


def test_login_and_protected_route(client, creds, db_url):
    import asyncio

    tenant, username, password = creds
    asyncio.run(_seed(db_url, tenant, username, password))

    res = _login(client, tenant, username, password)
    assert res.status_code == 200, res.text
    tokens = res.json()
    h = {"Authorization": f"Bearer {tokens['access_token']}"}

    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["tenant"] == tenant and me.json()["user"] == username

    # Route terlindungi: thread tak ada → 404 (bukan 401) = identitas diterima.
    tid = f"{tenant}:{username}:sess-{uuid.uuid4().hex[:8]}"
    state = client.get(f"/api/v1/threads/{tid}/state", headers=h)
    assert state.status_code == 404
    assert state.json()["code"] == "THREAD_NOT_FOUND"


def test_wrong_password_and_unknown_user_same_shape(client, creds, db_url):
    import asyncio

    tenant, username, password = creds
    asyncio.run(_seed(db_url, tenant, username, password))
    a = _login(client, tenant, username, "salah-banget")
    b = _login(client, tenant, "tidakada", password)
    assert a.status_code == b.status_code == 401
    assert a.json()["code"] == b.json()["code"] == "UNAUTHENTICATED"


def test_cross_tenant_thread_denied_with_real_token(client, creds, db_url):
    import asyncio

    tenant, username, password = creds
    asyncio.run(_seed(db_url, tenant, username, password))
    tokens = _login(client, tenant, username, password).json()
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    # Thread milik tenant lain (format valid, pemilik beda) → 403.
    other = f"toko-lain:{username}:sess-{uuid.uuid4().hex[:8]}"
    res = client.get(f"/api/v1/threads/{other}/state", headers=h)
    assert res.status_code == 403
    assert res.json()["code"] == "CROSS_TENANT_DENIED"


def test_refresh_rotation_and_logout_live(client, creds, db_url):
    import asyncio

    tenant, username, password = creds
    asyncio.run(_seed(db_url, tenant, username, password))
    tokens = _login(client, tenant, username, password).json()

    rotated = client.post("/api/v1/auth/refresh",
                          json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()
    # Refresh lama sudah di-revoke di Valkey.
    replay = client.post("/api/v1/auth/refresh",
                         json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401

    h = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=h,
                       json={"refresh_token": new["refresh_token"]}).status_code == 204
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_health_reports_cache_and_auth(client):
    body = client.get("/api/v1/health").json()
    assert body["checks"]["cache"] == "ok"
    assert body["checks"]["auth"].startswith("jwt")
    assert body["checks"]["checkpointer"].startswith("postgres")



