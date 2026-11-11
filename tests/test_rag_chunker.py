"""Unit chunker (D8). Fake tokenizer = hitung kata."""

from app.rag.chunker import (
    CHUNKER_VERSION,
    PRESETS,
    chunk_factoid_lines,
    chunk_markdown,
    chunk_pages,
    chunk_prose,
    chunk_text,
)


class FakeTok:
    name = "fake-ws"

    def count(self, text: str) -> int:
        return len(text.split())

    def truncate_words(self, text: str, max_tokens: int) -> str:
        return " ".join(text.split()[:max_tokens])


def test_preset_beku():
    assert PRESETS == {"prose": {"size": 512, "overlap": 64},
                       "markdown": {"size": 512, "overlap": 64},
                       "pdf": {"size": 512, "overlap": 64},
                       "factoid": {"size": 256, "overlap": 0}}


def test_prose_pendek_1_chunk():
    out = chunk_prose("Satu kalimat. Dua kalimat.", "prose", FakeTok())
    assert len(out) == 1
    assert out[0].ord == 0
    assert out[0].token_count == 4


def test_prose_panjang_pecah_dengan_overlap():
    sents = [f"Kalimat nomor {i} tentang toko." for i in range(200)]
    out = chunk_prose(" ".join(sents), "prose", FakeTok())
    assert len(out) > 1
    assert all(c.token_count <= 512 for c in out)
    # Overlap: chunk kedua memuat ekor chunk pertama.
    first_tail = out[0].content.split()[-10:]
    assert any(w in out[1].content for w in first_tail)


def test_markdown_bawa_header():
    md = "## Kebijakan Retur\nBarang bisa diretur 14 hari.\n\n### Syarat\nBawa nota."
    out = chunk_markdown(md, FakeTok())
    assert out
    assert "[Kebijakan Retur]" in out[0].content
    assert out[0].heading == "Kebijakan Retur"
    assert any("Syarat" in c.content for c in out)


def test_pdf_tak_lintas_halaman():
    pages = [{"page": 1, "text": "Halaman satu."},
             {"page": 2, "text": "Halaman dua."}]
    out = chunk_pages(pages, FakeTok())
    assert [c.page for c in out] == [1, 2]
    assert [c.ord for c in out] == [0, 1]


def test_factoid_1_baris_1_chunk():
    out = chunk_factoid_lines(["SKU-1 Rp 5000", "", "SKU-2 Rp 7000"], FakeTok())
    assert [c.content for c in out] == ["SKU-1 Rp 5000", "SKU-2 Rp 7000"]
    assert [c.ord for c in out] == [0, 1]


def test_factoid_panjang_dipotong():
    out = chunk_factoid_lines(["kata " * 300], FakeTok())
    assert out[0].token_count <= 256


def test_preset_asing_ditolak():
    try:
        chunk_text("x", "nano", FakeTok())
    except ValueError as exc:
        assert "nano" in str(exc)
    else:
        raise AssertionError("preset asing lolos")


def test_chunk_text_dispatcher():
    assert chunk_text("a b", "prose", FakeTok())[0].content == "a b"
    assert chunk_text("## H\nisi", "markdown", FakeTok())[0].heading == "H"


def test_unit_raksasa_jaga_urutan_dan_overlap():
    kecil = "Kalimat pembuka pendek."
    raksasa = " ".join(f"kata{i}" for i in range(700))
    out = chunk_prose(f"{kecil} {raksasa}", "prose", FakeTok())
    assert out[0].content.startswith("Kalimat pembuka")  # flush dulu (F-6)
    assert len(out) >= 3  # 1 kecil + ≥2 potongan raksasa + overlap
    assert "kata0" in out[1].content
    # Overlap: potongan ke-2 dan ke-3 berbagi kata ekor.
    if len(out) > 2:
        tail2 = set(out[1].content.split()[-10:])
        head3 = set(out[2].content.split()[:70])
        assert tail2 & head3


def test_prefix_header_masuk_budget():
    md = "## " + "Kata " * 40 + "\n\n" + "isi. " * 400
    out = chunk_markdown(md, FakeTok())
    assert out
    for c in out:
        assert c.token_count <= 512, f"jebol: {c.token_count}"  # F-7


def test_chunker_version_ada():
    assert isinstance(CHUNKER_VERSION, str) and CHUNKER_VERSION
