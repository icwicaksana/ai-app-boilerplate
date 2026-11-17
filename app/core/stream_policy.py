"""Kebijakan event streaming: event mana yang boleh sampai ke klien.

INVARIAN v0.9
    Hanya percakapan ROOT yang terlihat user. Run BERSARANG — subagent,
    klasifikasi internal, retry self-correction — tidak pernah.

KENAPA INI ADA
    `Runner.stream()` memakai `astream_events(version="v2")` yang melaporkan
    SEMUA pemanggilan model di dalam pohon run, bukan hanya milik graph root.
    Tanpa penyaring, token dari model mana pun yang berjalan di dalam request
    ikut terkirim sebagai `token` SSE.

    Sebelum v0.9 kebocoran ini belum muncul BUKAN karena ada penyaring,
    melainkan karena jalur yang punya LLM bersarang (pipeline agent) kebetulan
    memakai `run()` bukan `stream()`. Itu kebetulan arsitektur, bukan
    pertahanan.

TIGA LAPIS PERTAHANAN
    1. Struktural — LLM subagent dibangun dengan `disable_streaming=True`,
       sehingga `on_chat_model_stream` tidak pernah dihasilkan sama sekali.
    2. Kebijakan (berkas ini) — tag eksplisit `subagent`/`internal` menutup
       `tool_start`/`tool_end` internal dan token dari subagent yang lupa
       menyetel flag. Penandaan WAJIB lewat `make_subagent_tool()`.
    3. Tes regresi — membuktikan lapis 1+2 bekerja pada graph NYATA.

CATATAN: HEURISTIK KEDALAMAN DIBUANG (diukur, bukan diduga)
    Rencana awal menyaring berdasarkan kedalaman `checkpoint_ns`. Itu SALAH
    dan sudah dibuktikan pada repo ini: di dalam `create_agent`, model milik
    graph ROOT sendiri berjalan di namespace `model:<uuid>` (kedalaman 1) —
    SAMA dengan model child di `tools:<uuid>` (kedalaman 1). Kedalaman tidak
    membedakan induk dari anak. Satu-satunya pembeda adalah panjang
    `parent_ids` (root=2, child=5), dan angka itu bergantung pada struktur
    internal LangGraph sehingga tidak layak dijadikan invarian.

    Konsekuensinya: penandaan eksplisit BUKAN pilihan gaya, melainkan
    satu-satunya mekanisme yang stabil. Itulah alasan `make_subagent_tool()`
    bersifat WAJIB.

CATATAN: KETERGANTUNGAN PERILAKU LANGGRAPH
    Lapis 2 bertumpu pada "tag config merambat ke event model child" —
    perilaku yang terbukti pada langchain==1.4.6 / langchain-core==1.6.10 /
    langgraph==1.2.15 (diuji tests/test_stream_leak_regression.py) tetapi
    tidak dijamin API publik. Bila upgrade LangGraph membuat
    `test_tag_subagent_menutup_kebocoran` merah, JANGAN hijau-kan dengan
    mengubah tes — selidiki propagasi tag dulu.

Modul ini MURNI: tanpa I/O, tanpa import LangGraph, mudah dites langsung.
"""

from __future__ import annotations

from typing import Any

#: Jenis event yang memang bagian dari kontrak SSE (lihat schemas/stream.py).
#: Daftar ini SENGAJA sempit: event tak dikenal DITOLAK, bukan diteruskan.
CLIENT_EVENT_KINDS = frozenset({
    "on_chat_model_stream",
    "on_tool_start",
    "on_tool_end",
})

#: Tag penanda run yang tidak boleh dipancarkan ke klien.
#: Pemilik tiap tag (tag tanpa pemilik = risiko over-filter saat dipakai ulang):
#: * `subagent`  — dipasang oleh make_subagent_tool() (F4, wajib).
#: * `internal`  — model bantu (klasifikasi, ringkasan) yang bukan percakapan.
#: * `no-stream` — CADANGAN untuk integrasi pihak ketiga; belum ada produsen
#:   di repo ini. Jangan pakai untuk arti lain tanpa memperbarui komentar ini.
HIDDEN_TAGS = frozenset({"subagent", "internal", "no-stream"})


class StreamPolicy:
    """Menyaring event sebelum dipetakan ke SSE. Selalu gagal-tertutup."""

    def accept(self, event: dict[str, Any]) -> bool:
        """True bila event boleh sampai ke klien. Tidak pernah raise."""
        try:
            if event.get("event") not in CLIENT_EVENT_KINDS:
                return False

            # Validasi tipe EKSPLISIT, bukan mengandalkan exception:
            # `set("subagent")` menghasilkan himpunan huruf, bukan tag —
            # tanpa cek ini, string nyasar justru meloloskan event.
            tags_raw = event.get("tags") or ()
            if not isinstance(tags_raw, (list, tuple, set, frozenset)):
                return False
            if set(tags_raw) & HIDDEN_TAGS:
                return False

            return True
        except Exception:
            # Penyaring yang melempar exception akan menjatuhkan seluruh
            # stream; penyaring yang mengembalikan True membocorkan. Jadi:
            # tolak.
            return False

    def __repr__(self) -> str:
        return "StreamPolicy()"
