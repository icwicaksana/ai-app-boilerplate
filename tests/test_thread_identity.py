"""Thread-ID kanonis v0.3: parse/validasi/komposisi/kepemilikan (tanpa DB)."""

import pytest
from fastapi import Request

from app.controllers.base import BaseController
from app.core.config import Settings
from app.core.dependencies import get_thread_identity
from app.core.errors import (
    CrossWorkspaceError,
    InvalidIdentityError,
    InvalidThreadIdError,
)
from app.core.threads import (
    ThreadIdentity,
    compose_thread_id,
    ensure_owner,
    parse_thread_id,
)


def _req(headers: dict) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_parse_valid_lowercase_canon():
    assert parse_thread_id("Toko-A:Budi:Sess-7F3A") == ("toko-a", "budi", "sess-7f3a")


@pytest.mark.parametrize("bad", [
    "", "thr-abc123", "run-xyz", "tanpa-pemisah", "a:b", "t:u:sess-1",
    "to:u:sess-1",  # workspace < 3
    "toko:a:b",  # session < 4
    "TOKO A:b:sess-1",  # spasi
    "toko-a:b:sess-1:extra",  # 4 segmen
    "toko-a::sess-1",  # user kosong
    "x" * 33 + ":u:sess-1",  # workspace > 32
])
def test_parse_invalid_rejected(bad):
    with pytest.raises(InvalidThreadIdError) as ei:
        parse_thread_id(bad)
    assert ei.value.status_code == 400
    assert ei.value.code == "INVALID_THREAD_ID"


def test_compose_default_session():
    tid = compose_thread_id("Toko-A", "Budi")
    assert tid.startswith("toko-a:budi:sess-")
    parse_thread_id(tid)  # hasil compose selalu valid


def test_compose_explicit_session():
    with pytest.raises(InvalidThreadIdError):
        compose_thread_id("to", "u")  # workspace pendek
    assert compose_thread_id("toko-a", "budi", "Sess-1") == "toko-a:budi:sess-1"


def test_ensure_owner_match_and_mismatch():
    ident = ThreadIdentity(workspace="toko-a", user="budi")
    assert ensure_owner(ident, "toko-a:budi:sess-1") == ("toko-a", "budi", "sess-1")
    with pytest.raises(CrossWorkspaceError) as ei:
        ensure_owner(ident, "toko-b:budi:sess-1")
    assert ei.value.status_code == 403
    with pytest.raises(CrossWorkspaceError):
        ensure_owner(ident, "toko-a:ani:sess-1")
    with pytest.raises(InvalidThreadIdError):
        ensure_owner(ident, "thr-xxx")


def test_resolve_compose_and_continue():
    ctrl = BaseController(settings=None)
    ident = ThreadIdentity(workspace="toko-a", user="budi")
    tid, config = ctrl.resolve_thread_config(ident, None)
    assert tid.startswith("toko-a:budi:sess-")
    assert config == {"configurable": {"thread_id": tid}}
    tid2, _ = ctrl.resolve_thread_config(ident, "  TOKO-A:Budi:SESS-9  ")
    assert tid2 == "toko-a:budi:sess-9"  # kanon + lanjutkan, bukan baru
    with pytest.raises(CrossWorkspaceError):
        ctrl.resolve_thread_config(ident, "toko-b:budi:sess-1")
    with pytest.raises(InvalidThreadIdError):
        ctrl.resolve_thread_config(ident, "thr-lama")


@pytest.mark.asyncio
async def test_mode_trust_header_dihapus():
    """v0.8.1: mode `trusted_headers` DIHAPUS TOTAL.

    Header X-Tenant-ID / X-User-ID tidak lagi jalur identitas: Settings
    menolak mode itu, dan tanpa Bearer token identitas tak pernah dibentuk.
    """
    with pytest.raises(Exception):
        Settings(_env_file=None, AUTH_MODE="trusted_headers",
                 AUTH_ALLOW_TRUSTED_HEADERS=True)


@pytest.mark.asyncio
async def test_identity_jwt_mode_ignores_headers_without_token():
    """v0.4 default: header identitas TIDAK dipercaya; tanpa Bearer → 401."""
    from app.core.errors import UnauthenticatedError

    s = Settings(_env_file=None, AUTH_MODE="jwt", JWT_SECRET="x" * 40)
    with pytest.raises(UnauthenticatedError):
        await get_thread_identity(
            _req({"X-Tenant-ID": "toko-a", "X-User-ID": "budi"}),
            settings=s, cache=None)
