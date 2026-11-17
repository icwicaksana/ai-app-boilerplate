"""v0.9 hardening (P0-4): detail error yang aman untuk klien.

Kontrak: pesan operasional (4xx, 502/503) lewat apa adanya — user bisa
bertindak. Pesan 500 digenerikkan — isinya uraian wiring/path internal.
Exception tak dikenal hanya nama tipenya — pesannya bisa bawa path/DSN/dump
driver. Detail penuh selalu di log server (korelasi via request_id).
"""

from __future__ import annotations

from app.core.errors import (
    AgentBindingError,
    EmbeddingUnavailableError,
    UpstreamAIError,
    public_error_detail,
)


def test_500_digenerikkan():
    err = AgentBindingError("wiring salah (session='x', path=/rahasia/db)")
    assert public_error_detail(err) == "Terjadi kesalahan internal."
    assert "/rahasia" not in public_error_detail(err)


def test_operasional_lewat_apa_adanya():
    assert "RAG" in public_error_detail(EmbeddingUnavailableError("RAG mati"))
    assert "proxy" in public_error_detail(UpstreamAIError("proxy timeout"))


def test_tak_dikenal_hanya_nama_tipe():
    detail = public_error_detail(RuntimeError("bocor: /etc/passwd"))
    assert detail == "RuntimeError"
    assert "/etc" not in detail
