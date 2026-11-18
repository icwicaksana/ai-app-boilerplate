"""Probe ketersediaan layanan live (V1-E): skip jujur bila mati."""

from __future__ import annotations

import os
import urllib.request


def service_up(url: str, *, timeout: float = 2.0) -> bool:
    """True bila GET / (atau path di URL) menjawab 2xx/4xx."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def require_env(name: str, *fallbacks: str, default: str = "") -> str:
    """Ambil env berlapis (TEST_* → prod → default)."""
    for key in (name, *fallbacks):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return default


def gate_env() -> dict[str, str]:
    """URL layanan live untuk tier nightly (kosong = skip)."""
    return {
        "db_url": require_env("TEST_DATABASE_URL", "DATABASE_URL"),
        "embed_url": require_env("TEST_EMBED_BASE_URL",
                                 "RAG_EMBED_BASE_URL",
                                 default="http://localhost:7997"),
        "qdrant_url": require_env("TEST_QDRANT_URL", "QDRANT_URL",
                                  default="http://localhost:6333"),
        "litellm_url": require_env("TEST_LITELLM_URL", "LITELLM_BASE_URL"),
    }
