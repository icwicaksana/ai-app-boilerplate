"""v0.8.2: JWT dual-read klaim `tenant` (legacy) — termasuk date-bomb sunset.

Latar: v0.8.1 mengganti nama klaim `tenant` -> `workspace` tanpa fase
transisi, sehingga token yang terbit sebelum deploy langsung ditolak 401.
v0.8.2 menerima keduanya (`workspace` diutamakan) sampai LEGACY_CLAIM_SUNSET.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    ALGORITHM,
    CLAIM_WORKSPACE,
    LEGACY_CLAIM_SUNSET,
    LEGACY_CLAIM_WORKSPACE,
    TYPE_ACCESS,
    InvalidToken,
    decode_token,
)

SECRET = "x" * 40


def _settings() -> Settings:
    return Settings(_env_file=None, JWT_SECRET=SECRET,
                    JWT_ISSUER="ai-app-boilerplate")


def _forge(**overrides) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "budi",
        "jti": "jti-1",
        "typ": TYPE_ACCESS,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        "iss": "ai-app-boilerplate",
    }
    payload.update(overrides)
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def test_klaim_legacy_tenant_diterima():
    """Token pra-v0.8.1 (klaim `tenant`) harus 200, bukan 401."""
    claims = decode_token(_settings(), _forge(**{LEGACY_CLAIM_WORKSPACE: "acme"}))
    assert claims.workspace == "acme"
    assert claims.legacy_claim is True


def test_klaim_baru_workspace_diterima():
    claims = decode_token(_settings(), _forge(**{CLAIM_WORKSPACE: "acme"}))
    assert claims.workspace == "acme"
    assert claims.legacy_claim is False


def test_workspace_menang_bila_keduanya_ada():
    """Bila dua klaim berbeda, `workspace` yang baru WAJIB menang."""
    tok = _forge(**{CLAIM_WORKSPACE: "baru", LEGACY_CLAIM_WORKSPACE: "lama"})
    claims = decode_token(_settings(), tok)
    assert claims.workspace == "baru"
    assert claims.legacy_claim is False


def test_tanpa_klaim_identitas_ditolak():
    """`require` tidak lagi memuat nama klaim identitas -> dicek manual."""
    with pytest.raises(InvalidToken):
        decode_token(_settings(), _forge())


def test_typ_salah_tetap_ditolak_pada_token_legacy():
    """Dual-read tidak boleh melonggarkan pemisahan access/refresh."""
    with pytest.raises(InvalidToken):
        decode_token(_settings(),
                     _forge(typ="refresh", **{LEGACY_CLAIM_WORKSPACE: "acme"}),
                     expected_type=TYPE_ACCESS)


def test_sunset_belum_lewat():
    """DATE-BOMB: gagal setelah LEGACY_CLAIM_SUNSET, memaksa pencabutan.

    Kalau test ini merah, artinya dual-read sudah melewati tenggatnya:
    hapus cabang legacy di decode_token + hapus test legacy di berkas ini,
    lalu naikkan versi. Jangan menaikkan tanggalnya.
    """
    assert date.today() < LEGACY_CLAIM_SUNSET, (
        f"dual-read klaim '{LEGACY_CLAIM_WORKSPACE}' sudah melewati sunset "
        f"{LEGACY_CLAIM_SUNSET} — cabut sekarang (v0.9.0).")
