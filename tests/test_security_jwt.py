"""Unit: primitif JWT (tanpa I/O). Fokus: penolakan, bukan hanya happy path."""

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    ALGORITHM,
    TYPE_ACCESS,
    TYPE_REFRESH,
    InvalidToken,
    TokenExpired,
    create_access_token,
    create_refresh_token,
    create_token,
    decode_token,
    new_jti,
)

SECRET = "s" * 40
OTHER = "o" * 40


def _s(**kw) -> Settings:
    args = {"_env_file": None, "JWT_SECRET": SECRET}
    args.update(kw)
    return Settings(**args)


def test_roundtrip_access_claims():
    token, claims = create_access_token(_s(), tenant="toko-a", user="budi",
                                        scopes=("chat", "threads"))
    decoded = decode_token(_s(), token, expected_type=TYPE_ACCESS)
    assert decoded.subject == "budi" and decoded.tenant == "toko-a"
    assert decoded.scopes == ("chat", "threads")
    assert decoded.token_type == TYPE_ACCESS
    assert decoded.jti == claims.jti
    assert decoded.expires_at == claims.expires_at


def test_jti_unique_per_token():
    s = _s()
    a, _ = create_access_token(s, tenant="t", user="u")
    b, _ = create_access_token(s, tenant="t", user="u")
    assert decode_token(s, a).jti != decode_token(s, b).jti


def test_expired_token_rejected():
    token, _ = create_token(_s(), tenant="t", user="u", token_type=TYPE_ACCESS,
                            ttl_seconds=-60)
    with pytest.raises(TokenExpired):
        decode_token(_s(), token)


def test_wrong_secret_rejected():
    token, _ = create_access_token(_s(), tenant="t", user="u")
    with pytest.raises(InvalidToken):
        decode_token(_s(JWT_SECRET=OTHER), token)


def test_alg_none_confusion_rejected():
    """Token tanpa tanda tangan (alg=none) WAJIB ditolak."""
    forged = jwt.encode({"sub": "u", "tenant": "t", "typ": "access",
                         "jti": "x", "iss": "ai-app-boilerplate",
                         "iat": 0, "exp": 9999999999},
                        key="", algorithm="none")
    with pytest.raises(InvalidToken):
        decode_token(_s(), forged)


def test_refresh_cannot_be_used_as_access():
    token, _ = create_refresh_token(_s(), tenant="t", user="u")
    with pytest.raises(InvalidToken):
        decode_token(_s(), token, expected_type=TYPE_ACCESS)
    assert decode_token(_s(), token, expected_type=TYPE_REFRESH).token_type == TYPE_REFRESH


def test_issuer_mismatch_rejected():
    token, _ = create_access_token(_s(JWT_ISSUER="issuer-a"), tenant="t", user="u")
    with pytest.raises(InvalidToken):
        decode_token(_s(JWT_ISSUER="issuer-b"), token)


def test_missing_required_claim_rejected():
    """Token tanpa `jti` (tak bisa di-revoke) ditolak."""
    partial = jwt.encode({"sub": "u", "tenant": "t", "typ": "access",
                          "iss": "ai-app-boilerplate", "iat": 0,
                          "exp": 9999999999},
                         SECRET, algorithm=ALGORITHM)
    with pytest.raises(InvalidToken):
        decode_token(_s(), partial)


def test_empty_token_and_missing_secret():
    with pytest.raises(InvalidToken):
        decode_token(_s(), "")
    with pytest.raises(InvalidToken):
        decode_token(_s(JWT_SECRET=""), "apa-saja")


def test_ttl_remaining_positive_then_zero():
    _, claims = create_access_token(_s(), tenant="t", user="u")
    assert claims.ttl_remaining > 0
    expired, exp_claims = create_token(_s(), tenant="t", user="u",
                                       token_type=TYPE_ACCESS, ttl_seconds=-5)
    assert exp_claims.ttl_remaining == 0


def test_unknown_token_type_rejected():
    with pytest.raises(ValueError):
        create_token(_s(), tenant="t", user="u", token_type="session", ttl_seconds=60)


def test_new_jti_shape():
    assert len(new_jti()) == 32
