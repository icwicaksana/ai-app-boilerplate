"""Fernet envelope untuk secret konektor (D11).

Kunci: `SOURCE_CONN_KEY` (44ch base64, validasi di Settings). Rotasi manual
terdokumentasi (decrypt-dengan-kunci-lama → encrypt-dengan-kunci-baru).
"""

from __future__ import annotations


def encrypt_secret(plaintext: str, key_b64: str) -> bytes:
    """Enkripsi password/DSN → BYTEA untuk `source_connections.secret_enc`."""
    from cryptography.fernet import Fernet

    return Fernet(key_b64.encode()).encrypt(plaintext.encode())


def decrypt_secret(blob: bytes, key_b64: str) -> str:
    """Dekripsi. Kunci salah → raise (jangan telan: kredensial rusak)."""
    from cryptography.fernet import Fernet, InvalidToken

    try:
        return Fernet(key_b64.encode()).decrypt(bytes(blob)).decode()
    except InvalidToken as exc:
        raise ValueError(
            "secret konektor tak bisa dibuka (SOURCE_CONN_KEY salah "
            "atau blob korup — lihat runbook rotasi).") from exc
