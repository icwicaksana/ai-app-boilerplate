"""v0.9 F0-5/F0-6: factory AgentContext — binding yang hilang harus berisik.

Yang dibuktikan di sini adalah PEMBEDAAN JALUR, bukan perilaku agen:
D11 membolehkan chat berjalan tanpa retriever (RAG mati), tetapi jalur
resume-approval dan fork WAJIB gagal keras kalau binding hilang — karena
graph yang dibangun ulang harus identik dengan graph yang di-interrupt.
"""

from __future__ import annotations

import pytest

from app.agents.factory import build_agent_context
from app.core.config import Settings
from app.core.errors import AgentBindingError, EmbeddingUnavailableError


class _AgenRag:
    NEEDS_RETRIEVER = True


class _AgenBiasa:
    pass


def _settings() -> Settings:
    return Settings(_env_file=None, JWT_SECRET="x" * 40,
                    JWT_ISSUER="ai-app-boilerplate")


def _build(**kw):
    base = dict(settings=_settings(), checkpointer=None, runner=None,
                session_id="toko-a:budi:sess-1")
    base.update(kw)
    return build_agent_context(**base)


# --- jalur yang WAJIB gagal keras -----------------------------------------

def test_resume_tanpa_rag_gagal_keras():
    """Ini bug P0-2: dulu bound_tools jadi [] tanpa satu pun error."""
    with pytest.raises(AgentBindingError):
        _build(agent_cls=_AgenRag, missing_retriever="raise_binding")


def test_fork_tanpa_rag_gagal_keras():
    with pytest.raises(AgentBindingError):
        _build(agent_cls=_AgenRag, missing_retriever="raise_binding",
               pool=object(), rag=None)


def test_pesan_error_menyebut_jalur_penyebab():
    with pytest.raises(AgentBindingError) as exc:
        _build(agent_cls=_AgenRag, missing_retriever="raise_binding")
    pesan = str(exc.value)
    assert "resume-approval" in pesan
    assert "fork thread" in pesan


# --- jalur chat: perilaku LAMA dipertahankan ------------------------------

def test_chat_rag_mati_tetap_503():
    """D11 + perilaku v0.7: chat ber-RAG saat RAG mati -> 503 jelas."""
    with pytest.raises(EmbeddingUnavailableError):
        _build(agent_cls=_AgenRag, missing_retriever="raise_embedding")


def test_chat_degradasi_boleh_tanpa_retriever():
    """D11: degradasi disengaja — tidak boleh melempar."""
    ctx = _build(agent_cls=_AgenRag, missing_retriever="degrade")
    assert ctx.retriever is None


def test_agen_tanpa_kebutuhan_tidak_pernah_gagal():
    for mode in ("degrade", "raise_embedding", "raise_binding"):
        ctx = _build(agent_cls=_AgenBiasa, missing_retriever=mode)
        assert ctx.retriever is None, mode


# --- memori ---------------------------------------------------------------

def test_store_dibungkus_memory_store_per_user():
    """MemoryStore harus terikat namespace thread, bukan store mentah."""
    from app.infra.memory import MemoryStore

    ctx = _build(store=object())
    assert isinstance(ctx.store, MemoryStore)


def test_tanpa_store_memory_none():
    ctx = _build(store=None)
    assert ctx.store is None


def test_thread_id_tidak_valid_tidak_melempar_saat_degrade():
    ctx = _build(agent_cls=_AgenRag, session_id="bukan-thread",
                 missing_retriever="degrade")
    assert ctx.retriever is None
