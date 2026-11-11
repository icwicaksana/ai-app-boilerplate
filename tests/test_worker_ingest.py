"""Unit worker ingest: helper murni (tanpa DB/model)."""

import pytest

from app.core.errors import (EmbeddingUnavailableError, OcrFailedError,
                             UnsupportedMediaError)
from app.workers.ingest import (_OcrRequired, _TerminalInput, classify_error, compute_backoff,
                                normalize_vector, table_to_factoid)


def test_backoff_eksponensial_cap_jitter():
    b0 = compute_backoff(0)
    assert 5.0 <= b0 <= 6.5
    b3 = compute_backoff(3)
    assert 40.0 <= b3 <= 52.0
    b9 = compute_backoff(9)
    assert 300.0 <= b9 <= 390.0  # cap 300 + jitter


def test_normalize_lewati_bila_sudah_satu():
    v = [1.0, 0.0, 0.0]
    assert normalize_vector(v) is v
    out = normalize_vector([3.0, 4.0])
    assert abs(sum(x * x for x in out) ** 0.5 - 1.0) < 1e-9
    with pytest.raises(_TerminalInput):
        normalize_vector([0.0, 0.0])


def test_factoid_format():
    s = table_to_factoid("Harga", ["SKU", "Harga"], ["A1", "5000"])
    assert s == "[Sheet: Harga] SKU: A1 | Harga: 5000"
    s2 = table_to_factoid(None, ["A"], ["x"])
    assert s2 == "A: x"
    s3 = table_to_factoid(None, ["A", "B"], ["x", ""])
    assert s3 == "A: x"


def test_klasifikasi_terminal():
    assert classify_error(UnsupportedMediaError("x"), 0)[0] is False
    assert classify_error(UnsupportedMediaError("x"), 0)[1] == "UNSUPPORTED_MEDIA"
    retry, code, _ = classify_error(_OcrRequired("pindai"), 0)
    assert (retry, code) == (False, "OCR_REQUIRED")
    retry, code, _ = classify_error(FileNotFoundError("blob hilang"), 0)
    assert (retry, code) == (False, "BLOB_GONE")


def test_klasifikasi_ocr_retry_sekali():
    assert classify_error(OcrFailedError("x"), 0)[0] is True
    assert classify_error(OcrFailedError("x"), 1)[0] is True
    assert classify_error(OcrFailedError("x"), 2)[0] is False


def test_klasifikasi_retryable():
    assert classify_error(EmbeddingUnavailableError(), 0)[0] is True
    assert classify_error(TimeoutError("lambat"), 0)[1] == "UPSTREAM_TIMEOUT"
    assert classify_error(ValueError("aneh"), 0)[1] == "WORKER_ERROR"


def test_terminal_input_tanpa_retry():
    retry, code, _ = classify_error(_TerminalInput("BAD_PATH", "x"), 0)
    assert retry is False and code == "BAD_PATH"
