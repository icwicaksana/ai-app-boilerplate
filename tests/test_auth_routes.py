"""Unit: route auth end-to-end dengan fake user repo + fakeredis (tanpa DB).

Yang dikunci: anti-enumerasi (pesan generik), rotasi refresh, revoke via
denylist, dan kontrak error 401/422.
"""

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi.testclient import TestClient

from app.api.v1.routes.auth import get_auth_controller
from app.controllers.AuthController import AuthController
from app.core.errors import InvalidTokenError, StorageUnavailableError
from app.core.security import TYPE_ACCESS, create_access_token, hash_password
from app.infra import users as users_repo
from app.infra.cache import CacheClient
from app.schemas.auth import TokenPair
from tests._auth_helpers import app_with_settings, test_settings as make_settings

PASSWORD = "rahasia-kuat-123"
HASH = hash_password(PASSWORD)


def _user(**kw):
    base = {"id": 1, "workspace": "toko-a", "username": "budi",
            "password_hash": HASH, "scopes": "chat jobs threads",
            "is_active": True}
    base.update(kw)
    return base


@pytest.fixture
def cache() -> CacheClient:
    return CacheClient(FakeRedis(decode_responses=True))


@pytest.fixture
def client(monkeypatch, cache):
    """App dengan cache nyata (fakeredis) di state + controller auth."""
    settings = make_settings()
    app = app_with_settings()
    # Dependency `get_cache` membaca app.state.cache → WAJIB di-set agar
    # jalur revoke (logout/denylist) benar-benar diuji.
    app.state.cache = cache
    ctrl = AuthController(settings, pool=None, cache=cache)
    app.dependency_overrides[get_auth_controller] = lambda: ctrl
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def known_user(monkeypatch):
    async def _get_user(pool, workspace, username):
        if (workspace, username) == ("toko-a", "budi"):
            return _user()
        return None

    async def _mark_login(pool, user_id):
        return None

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", _mark_login)


def _login(client, password=PASSWORD, username="budi", workspace="toko-a"):
    return client.post("/api/v1/auth/login",
                       json={"workspace": workspace, "username": username,
                             "password": password})


def test_login_ok_returns_token_pair(client, known_user):
    res = _login(client)
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] == make_settings().ACCESS_TOKEN_TTL_SECONDS


def test_login_wrong_password_generic_401(client, known_user):
    res = _login(client, password="salah")
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"
    # Pesan TIDAK boleh membedakan "user tak ada" vs "password salah".
    assert "Kredensial" in res.json()["detail"]


def test_login_unknown_user_same_message(client, known_user):
    a = _login(client, password="salah")
    b = _login(client, username="tidak-ada")
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_login_inactive_user_rejected(client, monkeypatch):
    async def _get_user(pool, workspace, username):
        return _user(is_active=False)

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", lambda *a: None)
    assert _login(client).status_code == 401


def test_login_validation_422(client):
    res = client.post("/api/v1/auth/login",
                      json={"workspace": "TOKO-A", "username": "budi", "password": "x"})
    assert res.status_code == 422
    assert res.json()["code"] == "VALIDATION_ERROR"


def test_me_returns_claims(client, known_user):
    tokens = _login(client).json()
    res = client.get("/api/v1/auth/me",
                     headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert res.status_code == 200
    body = res.json()
    assert body["workspace"] == "toko-a" and body["user"] == "budi"
    assert "chat" in body["scopes"] and body["expires_at"] > 0


def test_refresh_rotates_and_old_refresh_rejected(client, known_user):
    tokens = _login(client).json()
    first = client.post("/api/v1/auth/refresh",
                        json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    new_refresh = first.json()["refresh_token"]
    assert new_refresh != tokens["refresh_token"]
    # Token lama sudah di-revoke (rotasi) → 401.
    replay = client.post("/api/v1/auth/refresh",
                         json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
    assert replay.json()["code"] == "INVALID_TOKEN"
    # Token baru tetap bisa dipakai.
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": new_refresh}).status_code == 200


def test_access_token_cannot_be_used_as_refresh(client, known_user):
    tokens = _login(client).json()
    res = client.post("/api/v1/auth/refresh",
                      json={"refresh_token": tokens["access_token"]})
    assert res.status_code == 401


def test_logout_revokes_access(client, known_user):
    tokens = _login(client).json()
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=h,
                       json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    after = client.get("/api/v1/auth/me", headers=h)
    assert after.status_code == 401
    assert after.json()["code"] == "INVALID_TOKEN"
    # Refresh ikut di-revoke.
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_without_body_still_works(client, known_user):
    tokens = _login(client).json()
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 204


def test_me_requires_token(client):
    res = client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.headers.get("www-authenticate", "").lower().startswith("bearer")


# --- v1.0 S1: kanonisasi case (tutup kolisi Budi/budi) ---

def test_canon_identity_pure():
    from app.core.errors import InvalidThreadIdError
    from app.infra.users import canon_identity

    assert canon_identity("Toko-A", "Budi") == ("toko-a", "budi")
    assert canon_identity("  toko-a ", "budi") == ("toko-a", "budi")
    with pytest.raises(InvalidThreadIdError):
        canon_identity("x", "budi")  # workspace < 3 char
    with pytest.raises(InvalidThreadIdError):
        canon_identity("toko-a", "Bu di")  # spasi tak valid


@pytest.mark.asyncio
async def test_login_normalizes_case_before_repo(monkeypatch, cache):
    """Controller meneruskan identitas kanonis ke repo (lapis-2)."""
    from app.core.security import decode_token

    seen = {}

    async def _get_user(pool, workspace, username):
        seen["args"] = (workspace, username)
        return _user()

    async def _mark_login(pool, user_id):
        return None

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", _mark_login)
    ctrl = AuthController(make_settings(), pool=None, cache=cache)
    pair = await ctrl.login(workspace="Toko-A", username="Budi",
                            password=PASSWORD)
    assert seen["args"] == ("toko-a", "budi")
    claims = decode_token(make_settings(), pair.access_token,
                          expected_type=TYPE_ACCESS)
    assert (claims.workspace, claims.subject) == ("toko-a", "budi")


@pytest.mark.asyncio
async def test_users_repo_normalizes_sql_params():
    """Repo menormalisasi SEBELUM SQL (lapis-1, mencakup pemanggil langsung)."""

    class _Cur:
        rowcount = 1

        def __init__(self, rec):
            self.rec = rec

        async def execute(self, query, params):
            self.rec.append(params)
            return self

        async def fetchone(self):
            return {"id": 7}

    class _Conn:
        def __init__(self, rec):
            self.rec = rec

        async def execute(self, query, params):
            return await _Cur(self.rec).execute(query, params)

    class _Ctx:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def __init__(self):
            self.params = []

        def connection(self):
            return _Ctx(_Conn(self.params))

    from app.infra import users as repo

    pool = _Pool()
    await repo.get_user(pool, "Toko-A", "Budi")
    await repo.create_user(pool, workspace="Toko-A", username="Budi",
                           password_hash="h")
    await repo.set_active(pool, workspace="Toko-A", username="Budi",
                          is_active=False)
    assert pool.params[0] == ("toko-a", "budi")
    assert pool.params[1][:2] == ("toko-a", "budi")
    assert pool.params[2][1:] == ("toko-a", "budi")


def test_claims_noncanonical_rejected_401(client, known_user):
    """Token non-kanonis (pra-v1.0/tempaan) ditolak, bukan di-lowercase."""
    tok, _ = create_access_token(make_settings(), workspace="Toko-A",
                                 user="Budi", scopes=("chat",))
    res = client.get("/api/v1/auth/me",
                     headers={"Authorization": f"Bearer {tok}"})
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_TOKEN"


# --- v1.0 S5: refresh atomik + fail-closed ---

@pytest.mark.asyncio
async def test_refresh_parallel_single_winner(monkeypatch, cache):
    """Dua refresh konkuren token sama: tepat 1 menang (SET NX atomik)."""
    import asyncio

    async def _get_user(pool, workspace, username):
        return _user()

    async def _mark_login(pool, user_id):
        return None

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", _mark_login)
    ctrl = AuthController(make_settings(), pool=None, cache=cache)
    pair = await ctrl.login(workspace="toko-a", username="budi",
                            password=PASSWORD)
    results = await asyncio.gather(
        ctrl.refresh(pair.refresh_token), ctrl.refresh(pair.refresh_token),
        return_exceptions=True)
    oks = [r for r in results if isinstance(r, TokenPair)]
    fails = [r for r in results if isinstance(r, InvalidTokenError)]
    assert len(oks) == 1, results
    assert len(fails) == 1, results


@pytest.mark.asyncio
async def test_refresh_without_cache_is_503_not_blind_token(monkeypatch):
    """Refresh tanpa cache = 503 (fail-closed), bukan token tanpa rotasi."""

    async def _get_user(pool, workspace, username):
        return _user()

    async def _mark_login(pool, user_id):
        return None

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", _mark_login)
    ctrl = AuthController(make_settings(), pool=None, cache=None)
    pair = await ctrl.login(workspace="toko-a", username="budi",
                            password=PASSWORD)
    with pytest.raises(StorageUnavailableError):
        await ctrl.refresh(pair.refresh_token)


def test_refresh_without_cache_route_503(monkeypatch):
    """Jalur HTTP: refresh tanpa cache → 503 STORAGE_UNAVAILABLE."""

    async def _get_user(pool, workspace, username):
        if (workspace, username) == ("toko-a", "budi"):
            return _user()
        return None

    async def _mark_login(pool, user_id):
        return None

    monkeypatch.setattr(users_repo, "get_user", _get_user)
    monkeypatch.setattr(users_repo, "mark_login", _mark_login)
    settings = make_settings()
    app = app_with_settings()
    # SENGAJA tanpa app.state.cache → get_cache None → fail-closed.
    ctrl = AuthController(settings, pool=None, cache=None)
    app.dependency_overrides[get_auth_controller] = lambda: ctrl
    client = TestClient(app, raise_server_exceptions=False)
    tokens = client.post("/api/v1/auth/login",
                         json={"workspace": "toko-a", "username": "budi",
                               "password": PASSWORD}).json()
    res = client.post("/api/v1/auth/refresh",
                      json={"refresh_token": tokens["refresh_token"]})
    assert res.status_code == 503
    assert res.json()["code"] == "STORAGE_UNAVAILABLE"
