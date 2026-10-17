"""Memori jangka panjang per user (v0.3): profil + fakta lintas thread.

- Backend: `AsyncPostgresStore` mentah dari lifespan (pool bersama).
- Namespace TERKUNCI per instance: `("tenants", t, "users", u)` — pemanggil tak
  bisa salah alamat ke user lain (isolasi struktural, bukan filter manual).
- Key: `"profile"` (dict, merge per patch) + `"fact:<12hex>"` (fakta satuan).
- BUKAN RAG: fakta kecil (KB), tanpa embedding. Dokumen = v0.7.

Fail-soft: `get_profile`/`search_facts` mengembalikan kosong bila store raise —
memori tak boleh mematahkan chat (D-4). `put_*` membiarkan raise agar pemanggil
(tool/heuristic) bisa log + putuskan sendiri.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

PROFILE_KEY = "profile"
FACT_PREFIX = "fact:"
MAX_FACT_CHARS = 500
MAX_PROFILE_CHARS = 1000

# ID stabil pesan profil di checkpoint: add_messages REPLACE (bukan append)
# bila id sama — mencegah profil menumpuk tiap turn (1 profil selalu).
PROFILE_MSG_ID = "user-profile-v1"

# Filter rahasia BERSAMA tool + heuristik (satu sumber — jangan duplikat).
# Catatan: `\bpin\b` (word-boundary) agar "pintu"/"opini" tak ikut ditolak.
_SECRET_RES = (
    re.compile(r"password|kata\s*sandi|pass\s*word", re.IGNORECASE),
    re.compile(r"\b(otp|pin|cvv|cvc)\b", re.IGNORECASE),
    re.compile(r"nomor\s+kartu|card\s*number|no\s*kartu", re.IGNORECASE),
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),  # mirip PAN kartu
)


def looks_like_secret(text: str) -> bool:
    """Heuristik rahasia (password/OTP/PIN/kartu). Murni, mudah di-test."""
    if not text:
        return False
    return any(rx.search(text) for rx in _SECRET_RES)


def namespace_for(tenant: str, user: str) -> tuple[str, str, str, str]:
    """Namespace kanonis (BEKU): ("tenants", t, "users", u).

    Lowercase defensif: namespace Postgres case-sensitive, identitas kanonis
    selalu lowercase — tanpa ini "Toko-A" vs "toko-a" jadi 2 profil terpisah.
    """
    return ("tenants", tenant.strip().lower(), "users", user.strip().lower())


class MemoryStore:
    """Wrapper store yang terkunci ke 1 user. Murah (tanpa I/O saat init)."""

    def __init__(self, raw_store: Any, tenant: str, user: str):
        self._store = raw_store
        self.namespace = namespace_for(tenant, user)

    async def _read_profile_raw(self) -> dict[str, Any]:
        """Baca profil TANPA fail-soft (raise bila gagal). Untuk jalur tulis."""
        item = await self._store.aget(self.namespace, PROFILE_KEY)
        value = getattr(item, "value", None) if item is not None else None
        return dict(value) if isinstance(value, dict) else {}

    async def get_profile(self) -> dict[str, Any]:
        """Profil user ({} bila kosong/gagal — fail-soft, untuk jalur baca)."""
        try:
            return await self._read_profile_raw()
        except Exception:
            log.exception("memory get_profile gagal (lanjut tanpa profil)")
            return {}

    async def put_profile(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge patch ke profil (baca-timpah-tulis). Kembalikan profil akhir.

        Baca basis via jalur KERAS (`_read_profile_raw`): bila baca gagal,
        raise SEBELUM tulis — jangan merge di atas `{}` fail-soft (profil
        lama ikut terhapus permanen).
        """
        try:
            current = await self._read_profile_raw()
            merged = {**current, **{k: v for k, v in patch.items() if v is not None}}
            await self._store.aput(self.namespace, PROFILE_KEY, merged)
            return merged
        except Exception:
            log.exception("memory put_profile gagal")
            raise

    async def put_fact(self, text: str, key: str | None = None) -> str:
        """Simpan 1 fakta (cap 500 char). Kembalikan key yang dipakai."""
        clean = (text or "").strip()[:MAX_FACT_CHARS]
        if not clean:
            raise ValueError("fact kosong — tolak tulis sampah ke store.")
        fact_key = key or f"{FACT_PREFIX}{uuid.uuid4().hex[:12]}"
        try:
            await self._store.aput(self.namespace, fact_key, {"text": clean})
            return fact_key
        except Exception:
            log.exception("memory put_fact gagal")
            raise

    async def search_facts(self, query: str, limit: int = 5) -> list[str]:
        """Cari fakta relevan ([] bila kosong/gagal — fail-soft)."""
        try:
            items = await self._store.asearch(
                self.namespace, query=query, limit=max(1, min(limit, 20))
            )
        except Exception:
            log.exception("memory search_facts gagal (lanjut tanpa fakta)")
            return []
        out: list[str] = []
        for item in items or []:
            value = getattr(item, "value", None)
            if isinstance(value, dict) and isinstance(value.get("text"), str):
                out.append(value["text"])
        return out


MAX_FACTS_IN_PROMPT = 3
MAX_FACT_PROMPT_CHARS = 200


def profile_to_prompt_text(profile: dict[str, Any],
                            facts: list[str] | None = None) -> str:
    """Profil dict + fakta relevan → teks prompt (cap 1000 char). Murni."""
    parts = [f"{k}: {v}" for k, v in (profile or {}).items() if v not in (None, "")]
    text = "; ".join(parts)
    if facts:
        joined = " | ".join(f[:MAX_FACT_PROMPT_CHARS] for f in facts[:MAX_FACTS_IN_PROMPT])
        if joined:
            text = f"{text}; Fakta: {joined}" if text else f"Fakta: {joined}"
    text = text[:MAX_PROFILE_CHARS]
    return f"Profil user tersimpan: {text}" if text else ""
