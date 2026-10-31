"""Unit: hash password (Argon2id) — tak ada plaintext, verifikasi aman."""

import pytest

from app.core.security import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies():
    raw = "rahasia-kuat-123"
    hashed = hash_password(raw)
    assert raw not in hashed
    assert hashed.startswith("$argon2")
    assert verify_password(raw, hashed) is True


def test_wrong_password_rejected():
    hashed = hash_password("benar")
    assert verify_password("salah", hashed) is False


def test_salt_unique_per_hash():
    a = hash_password("sama")
    b = hash_password("sama")
    assert a != b  # salt acak
    assert verify_password("sama", a) and verify_password("sama", b)


def test_empty_password_rejected_on_hash():
    with pytest.raises(ValueError):
        hash_password("")


def test_corrupt_or_unknown_hash_returns_false():
    """Hash rusak TIDAK boleh raise (mencegah 500 saat data korup)."""
    assert verify_password("x", "bukan-hash") is False
    assert verify_password("x", "") is False
    assert verify_password("", "bukan-hash") is False


def test_unicode_password_supported():
    raw = "kata-sandi-ñ-日本語"
    assert verify_password(raw, hash_password(raw)) is True
