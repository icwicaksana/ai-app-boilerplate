"""Identitas thread kanonis v0.3+: `tenant:user:session` (BEKU permanen).

- Format: `^[a-z0-9-]{3,32}:[a-z0-9-_]{1,64}:[a-z0-9-]{4,64}$`.
- Normalisasi: lowercase dulu, baru validasi (header `Toko-A` → `toko-a`).
- v0.3: workspace/user dari header tepercaya (`X-Tenant-ID`/`X-User-ID`). v0.4 dari JWT.
- Format lama (`thr-xxx`, `run-xxx`, uuid polos) DITOLAK 400 — tanpa pengecualian.

Satu-satunya tempat parse/susun thread-ID. Controller/route dilarang regex sendiri.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.core.errors import CrossWorkspaceError, InvalidThreadIdError

THREAD_RE = re.compile(r"^[a-z0-9-]{3,32}:[a-z0-9-_]{1,64}:[a-z0-9-]{4,64}$")
WORKSPACE_RE = re.compile(r"^[a-z0-9-]{3,32}$")
USER_RE = re.compile(r"^[a-z0-9-_]{1,64}$")
SESSION_RE = re.compile(r"^[a-z0-9-]{4,64}$")


@dataclass(frozen=True)
class ThreadIdentity:
    """Siapa pemanggil (dari header tepercaya). v0.4: dari klaim JWT."""

    workspace: str
    user: str

    def __str__(self) -> str:
        # Format "workspace:user" untuk log server (log = privat ops, butuh ini
        # untuk korelasi; JANGAN tampilkan ke klien lain / respons publik).
        return f"{self.workspace}:{self.user}"


def canon_segment(value: str | None, name: str) -> str:
    """Kanonisasi 1 segmen identitas (strip + lowercase + validasi charset).

    Satu-satunya tempat normalisasi case/whitespace — pemanggil (termasuk
    `namespace_for` di infra/memory) dilarang `.strip().lower()` sendiri.
    """
    seg_re = {"workspace": WORKSPACE_RE, "user": USER_RE, "session": SESSION_RE}.get(name)
    if seg_re is None:
        raise ValueError(f"segmen identitas tak dikenal: {name!r}")
    seg = (value or "").strip().lower()
    if not seg_re.match(seg):
        # Bungkus sebagai thread_id agar constructor memberi 1 pesan final
        # yang jelas (tanpa double-wrap): "thread_id 'session:xxx' tidak valid".
        raise InvalidThreadIdError(f"{name}:{value}")
    return seg


def parse_thread_id(raw: str) -> tuple[str, str, str]:
    """Parse + validasi → (workspace, user, session). Raise InvalidThreadIdError."""
    tid = (raw or "").strip().lower()
    if not THREAD_RE.match(tid):
        raise InvalidThreadIdError(raw or "")
    workspace, user, session = tid.split(":")
    return workspace, user, session


def compose_thread_id(workspace: str, user: str, session: str | None = None) -> str:
    """Susun ID kanonis. session kosong → `sess-<12hex>` dari server."""
    t = canon_segment(workspace, "workspace")
    u = canon_segment(user, "user")
    s = canon_segment(session, "session") if session else f"sess-{uuid.uuid4().hex[:12]}"
    return f"{t}:{u}:{s}"


def ensure_owner(identity: ThreadIdentity, thread_id: str) -> tuple[str, str, str]:
    """Parse + pastikan thread milik pemanggil. Beda workspace/user → 403."""
    workspace, user, session = parse_thread_id(thread_id)
    if workspace != identity.workspace or user != identity.user:
        raise CrossWorkspaceError()
    return workspace, user, session
