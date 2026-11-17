"""v0.9 hardening (P0-4): detail error yang aman untuk klien.

Kontrak: pesan operasional (4xx, 502/503) lewat apa adanya — user bisa
bertindak. Pesan 500 digenerikkan — isinya uraian wiring/path internal.
Exception tak dikenal hanya nama tipenya — pesannya bisa bawa path/DSN/dump
driver. Detail penuh selalu di log server (korelasi via request_id).
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.errors import (
    AgentBindingError,
    AppError,
    EmbeddingUnavailableError,
    ForbiddenError,
    RateLimitedError,
    UpstreamAIError,
    public_error_detail,
    to_public_error,
)
from app.helpers.response import stream_error_from


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


# --- v1.0 R12+R14: mapper terpusat (satu per keluarga) ---

def _fake_exc(modul: str, nama: str, pesan: str = "x",
              status_code=None) -> Exception:
    cls = type(nama, (Exception,), {"__module__": modul})
    inst = cls(pesan)
    if status_code is not None:
        inst.status_code = status_code
    return inst


def test_mapper_teruskan_apperror():
    err = RateLimitedError(30, 20)
    assert to_public_error(err) is err
    err = ForbiddenError("tak boleh")
    mapped = to_public_error(err)
    assert (mapped.status_code, mapped.code) == (403, "FORBIDDEN")


def test_mapper_tak_dikenal_500_generik():
    mapped = to_public_error(RuntimeError("bocor: postgresql://u:p@h/db"))
    assert (mapped.status_code, mapped.code) == (500, "INTERNAL_ERROR")
    assert "postgresql" not in public_error_detail(mapped)


def test_mapper_timeout_504():
    for exc in (asyncio.TimeoutError("lambat"),
                _fake_exc("httpx", "ConnectTimeout", "c"),
                _fake_exc("litellm.exceptions", "APITimeoutError", "t"),
                _fake_exc("openai", "APITimeoutError", "t")):
        mapped = to_public_error(exc)
        assert (mapped.status_code, mapped.code) == (504, "UPSTREAM_TIMEOUT"), exc


def test_mapper_koneksi_502():
    for exc in (ConnectionRefusedError("tolak"),
                _fake_exc("httpx", "ConnectError", "c"),
                _fake_exc("openai", "APIConnectionError", "c"),
                _fake_exc("qdrant_client.http.exceptions", "ServiceUnavailable",
                          "s")):
        mapped = to_public_error(exc)
        assert (mapped.status_code, mapped.code) == (502, "UPSTREAM_UNAVAILABLE"), exc


def test_mapper_respons_5xx_sdk_502():
    exc = _fake_exc("anthropic", "InternalServerError", "e", status_code=503)
    mapped = to_public_error(exc)
    assert (mapped.status_code, mapped.code) == (502, "UPSTREAM_UNAVAILABLE")
    exc = _fake_exc("litellm.exceptions", "InternalServerError", "e")
    mapped = to_public_error(exc)
    assert (mapped.status_code, mapped.code) == (502, "UPSTREAM_ERROR")


def test_mapper_pool_habis_503():
    exc = _fake_exc("psycopg_pool", "PoolTimeout", "habis")
    mapped = to_public_error(exc)
    assert (mapped.status_code, mapped.code) == (503, "STORAGE_UNAVAILABLE")


def test_mapper_httpx_asli():
    import httpx

    mapped = to_public_error(httpx.ConnectError("dns down"))
    assert (mapped.status_code, mapped.code) == (502, "UPSTREAM_UNAVAILABLE")


def test_mapper_jangan_petakan_pembatalan():
    with pytest.raises(asyncio.CancelledError):
        to_public_error(asyncio.CancelledError())
    with pytest.raises(GeneratorExit):
        to_public_error(GeneratorExit())


def test_stream_error_from_bawa_status_code():
    ev = stream_error_from(RateLimitedError(30, 20), request_id="r1")
    assert ev["type"] == "error"
    assert (ev["status"], ev["code"]) == (429, "RATE_LIMITED")
    assert ev["instance"] == "r1"


def test_stream_error_from_hulu_dan_tak_dikenal():
    import httpx

    ev = stream_error_from(httpx.ConnectError("postgresql://rahasia"))
    assert (ev["status"], ev["code"]) == (502, "UPSTREAM_UNAVAILABLE")
    assert "postgresql" not in ev["detail"]
    ev = stream_error_from(ValueError("/rahasia/path"))
    assert (ev["status"], ev["code"]) == (500, "INTERNAL_ERROR")
    assert "/rahasia" not in ev["detail"]


def test_tak_ada_lagi_fstring_bocor_di_stream():
    """Regresi R12: tak ada pola f-string mentah di titik stream."""
    import pathlib

    for rel in ("app/agents/BaseChatAgent.py", "app/controllers/runner.py",
                "app/core/turns.py", "app/api/v1/routes/ws.py"):
        text = pathlib.Path(rel).read_text(encoding="utf-8")
        assert '{type(exc).__name__}: {exc}' not in text, rel
