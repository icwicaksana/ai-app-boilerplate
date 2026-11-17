"""v0.9 F4: kontrak Brief/Report — gagal tak boleh terlihat sukses."""

from __future__ import annotations

import pytest

from app.schemas.subagent import Brief, Report, ToolFailure


def test_brief_valid():
    b = Brief(task="Cek stok A", constraints=("jangan ubah data",))
    assert b.task == "Cek stok A"
    assert b.constraints == ("jangan ubah data",)


def test_brief_kosong_ditolak():
    with pytest.raises(ValueError, match="wajib diisi"):
        Brief(task="   ")


def test_brief_terlalu_panjang_ditolak():
    with pytest.raises(ValueError, match="melebihi"):
        Brief(task="x" * 4001)


def test_brief_constraints_dibatasi():
    with pytest.raises(ValueError, match="maksimum 8"):
        Brief(task="t", constraints=tuple(f"c{i}" for i in range(9)))
    with pytest.raises(ValueError, match="constraint"):
        Brief(task="t", constraints=("x" * 501,))


def test_brief_budget_harus_positif():
    with pytest.raises(ValueError, match="positif"):
        Brief(task="t", budget_tokens=0)


def test_report_ok():
    r = Report(status="ok", answer="jadi", tokens_used=10, ms=5)
    assert "Subagent: ok" in r.to_text()
    assert r.to_dict()["status"] == "ok"


def test_report_ok_tak_boleh_bawa_failures():
    with pytest.raises(ValueError, match="partial"):
        Report(status="ok", answer="x",
               failures=(ToolFailure(tool="t", error_class="timeout"),))


def test_report_partial_failed_wajib_sebut_gagal():
    with pytest.raises(ValueError, match="wajib menyebut"):
        Report(status="partial", answer="x")
    with pytest.raises(ValueError, match="wajib menyebut"):
        Report(status="failed")


def test_report_gagal_memerintah_jujur():
    r = Report(status="failed", failures=(
        ToolFailure(tool="cek", error_class="timeout", attempts=3),))
    text = r.to_text()
    assert "GAGAL cek" in text
    assert "TIDAK bisa didapat" in text


def test_report_status_tak_dikenal_ditolak():
    with pytest.raises(ValueError, match="tak dikenal"):
        Report(status="mungkin")  # type: ignore[arg-type]
