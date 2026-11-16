"""v0.9 F0: StreamPolicy — event mana yang boleh sampai ke klien.

Unit murni: tanpa graph, tanpa LLM. Bukti pada graph NYATA ada di
tests/test_stream_leak_regression.py.
"""

from __future__ import annotations

from app.core.stream_policy import (
    CLIENT_EVENT_KINDS,
    HIDDEN_TAGS,
    StreamPolicy,
)


def _ev(kind: str = "on_chat_model_stream", **kw) -> dict:
    base = {"event": kind, "tags": [], "metadata": {"checkpoint_ns": ""}}
    base.update(kw)
    return base


# --- event normal diterima -------------------------------------------------

def test_event_root_diterima():
    p = StreamPolicy()
    for kind in CLIENT_EVENT_KINDS:
        assert p.accept(_ev(kind)) is True, kind


def test_tag_tak_dikenal_tidak_menghalangi():
    assert StreamPolicy().accept(_ev(tags=["some-other-tag"])) is True


# --- penandaan tersembunyi -------------------------------------------------

def test_tag_subagent_ditolak():
    """Lapis 2: menutup subagent yang lupa disable_streaming."""
    p = StreamPolicy()
    for tag in ("subagent", "internal", "no-stream"):
        assert p.accept(_ev(tags=[tag])) is False, tag


def test_tag_tersembunyi_di_tengah_daftar_tetap_ditolak():
    p = StreamPolicy()
    assert p.accept(_ev(tags=["seq:step:1", "subagent", "x"])) is False


# --- event tak dikenal ditolak --------------------------------------------

def test_event_tak_dikenal_ditolak():
    """Daftar sengaja sempit: event baru harus didaftarkan sadar."""
    p = StreamPolicy()
    for kind in ("on_chain_start", "on_chat_model_end",
                 "on_retriever_start", "on_chain_stream"):
        assert p.accept(_ev(kind)) is False, kind


# --- fail-closed -----------------------------------------------------------

def test_event_rusak_ditolak_bukan_raise():
    p = StreamPolicy()
    assert p.accept({}) is False
    assert p.accept({"event": "on_chat_model_stream",
                     "tags": "subagent"}) is False, (
        "tags berupa string harus DITOLAK, bukan diubah jadi himpunan huruf")


def test_hidden_tags_tidak_tumpang_tindih_dengan_client_kinds():
    assert not (HIDDEN_TAGS & CLIENT_EVENT_KINDS)
