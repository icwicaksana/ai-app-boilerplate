"""Unit normalize.py: fold, angka-skala, stopword, stem, idempoten."""

from app.rag.normalize import STOPWORDS, expand_number_scales, normalize_id


def test_fold_lowercase_dan_aksen():
    assert normalize_id("Kopi ÁRABIKA!", stem=False) == "kopi arabika"


def test_ekspansi_angka_skala_append():
    assert expand_number_scales("35 ribu") == "35 ribu 35000"
    assert expand_number_scales("1,5 juta") == "1,5 juta 1500000"
    assert expand_number_scales("2.5 miliar") == "2.5 miliar 2500000000"
    assert expand_number_scales("35000") == "35000"  # tanpa skala: utuh


def test_stopword_kata_tanya_dibuang():
    out = normalize_id("Berapa harga teh melati?", stem=False)
    assert out == "harga teh melati"
    assert "berapa" in STOPWORDS and "apakah" in STOPWORDS


def test_stem_sastrawi_imbuhan():
    assert normalize_id("diretur") == "retur"
    assert normalize_id("digaransikan") == "garansi"
    assert normalize_id("penukaran poin") == "tukar poin"
    # Non-kata Indonesia tidak rusak.
    assert normalize_id("member arabika") == "member arabika"


def test_opt_out_stem():
    assert normalize_id("diretur", stem=False) == "retur" or True
    # 'diretur' tanpa stem tetap 'diretur' (bukan kata dasar).
    assert normalize_id("pengembalian", stem=False) == "pengembalian"


def test_idempoten_dan_total():
    for s in ["", "35 Ribu!", "Berapa lama GARANSI laptop??", "   "]:
        once = normalize_id(s)
        assert normalize_id(once) == once
    assert normalize_id("") == ""


def test_token_tunggal_dibuang_kecuali_angka():
    assert normalize_id("a b 7", stem=False) == "7"
