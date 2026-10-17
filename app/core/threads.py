"""Identitas thread kanonis v0.3+: `tenant:user:session` (BEKU permanen).

- Format: `^[a-z0-9-]{3,32}:[a-z0-9-_]{1,64}:[a-z0-9-]{4,64}$`.
- Normalisasi: lowercase dulu, baru validasi (header `Toko-A` → `toko-a`).
- v0.3: tenant/user dari header tepercaya (`X-Tenant-ID`/`X-User-ID`). v0.4 dari JWT.
- Format lama (`thr-xxx`, `run-xxx`, uuid polos) DITOLAK 400 — tanpa pengecualian.

Satu-satunya tempat parse/susun thread-ID. Controller/route dilarang regex sendiri.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.core.errors import CrossTenantError, InvalidThreadIdError

THREAD_RE = re.compile(r"^[a-z0-9-]{3,32}:[a-z0-9-_]{1,64}:[a-z0-9-]{4,64}$")
TENANT_RE = re.compile(r"^[a-z0-9-]{3,32}$")
USER_RE = re.compile(r"^[a-z0-9-_]{1,64}$")
SESSION_RE = re.compile(r"^[a-z0-9-]{4,64}$")


@dataclass(frozen=True)
class ThreadIdentity:
    """Siapa pemanggil (dari header tepercaya). v0.4: dari klaim JWT."""

    tenant: str
    user: str

    def __str__(self) -> str:
        # Format "tenant:user" untuk log server (log = privat ops, butuh ini
        # untuk korelasi; JANGAN tampilkan ke klien lain / respons publik).
        return f"{self.tenant}:{self.user}"


def _canon_seg(value: str, seg_re: re.Pattern, name: str) -> str:
    seg = (value or "").strip().lower()
    if not seg_re.match(seg):
        raise InvalidThreadIdError(f"<{name} '{value}' tak memenuhi pola>")
    return seg


def parse_thread_id(raw: str) -> tuple[str, str, str]:
    """Parse + validasi → (tenant, user, session). Raise InvalidThreadIdError."""
    tid = (raw or "").strip().lower()
    if not THREAD_RE.match(tid):
        raise InvalidThreadIdError(raw or "")
    tenant, user, session = tid.split(":")
    return tenant, user, session


def compose_thread_id(tenant: str, user: str, session: str | None = None) -> str:
    """Susun ID kanonis. session kosong → `sess-<12hex>` dari server."""
    t = _canon_seg(tenant, TENANT_RE, "tenant")
    u = _canon_seg(user, USER_RE, "user")
    s = session.strip().lower() if session else f"sess-{uuid.uuid4().hex[:12]}"
    if not SESSION_RE.match(s):
        raise InvalidThreadIdError(f"{t}:{u}:{session}")
    return f"{t}:{u}:{s}"


def ensure_owner(identity: ThreadIdentity, thread_id: str) -> tuple[str, str, str]:
    """Parse + pastikan thread milik pemanggil. Beda tenant/user → 403."""
    tenant, user, session = parse_thread_id(thread_id)
    if tenant != identity.tenant or user != identity.user:
        raise CrossTenantError()
    return tenant, user, session
