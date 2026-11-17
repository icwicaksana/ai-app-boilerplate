"""v0.9 F5: scaffold — render murni + lint baca-saja.

`os.mkdir` diblokir sandbox pada mesin ini, jadi tes TIDAK menjalankan
main() yang menulis disk. Yang diuji: (a) render selalu valid (parse +
compile), (b) nama tak valid ditolak dengan pesan jelas, (c) lint di atas
fixture DI-COMMIT. main() tinggal pembungkus tipis argparse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "skills_boot"


def test_render_skill_selalu_valid():
    from scripts.new_skill import render_skill_file

    from app.skills.parser import parse_skill

    text = render_skill_file("sop-garansi")
    skill = parse_skill(text, source="sop-garansi/SKILL.md")
    assert skill.name == "sop-garansi"
    assert skill.description and skill.when_to_use


def test_render_skill_nama_tak_valid():
    from scripts.new_skill import render_skill_file

    with pytest.raises(ValueError, match="tak valid"):
        render_skill_file("SOP BESAR")


def test_render_subagent_selalu_compile():
    from scripts.new_subagent import module_name_for, render_subagent_module

    module, text = render_subagent_module("cek-pesanan")
    assert module == "cek_pesanan"
    assert 'name="cek-pesanan"' in text
    assert "register(SubagentSpec(" in text
    compile(text, "<cek_pesanan>", "exec")


def test_render_subagent_nama_tak_valid():
    from scripts.new_subagent import render_subagent_module

    with pytest.raises(ValueError, match="tak valid"):
        render_subagent_module("nama berspasi!")


def test_lint_good_bersih():
    from scripts.skills_lint import lint_skills

    report = lint_skills(FIXTURES / "good")
    assert report["count"] == 2
    assert report["errors"] == []
    assert len(report["manifest"]) == 64
    assert report["index_chars"] > 0


def test_lint_mixed_melaporkan_3_error():
    from scripts.skills_lint import lint_skills

    report = lint_skills(FIXTURES / "mixed")
    assert report["count"] == 1
    assert len(report["errors"]) == 3


def test_lint_roster_demo_bersih():
    import app.agents.subagents  # noqa: F401  (pastikan demo terdaftar)
    from scripts.skills_lint import lint_subagents

    report = lint_subagents()
    assert "demo-stok" in report["specs"]
    assert report["errors"] == []


def test_lint_menangkap_spec_nakal(monkeypatch):
    from app.agents.subagents.registry import SUBAGENTS, SubagentSpec
    from scripts.skills_lint import lint_subagents

    monkeypatch.setitem(SUBAGENTS, "nakal", SubagentSpec(
        name="nakal", description="x", when_to_use="y",
        system_prompt="z", tools=("process_refund", "subagent__a", "tak-ada")))
    report = lint_subagents()
    assert any("approval-gated" in e for e in report["errors"])
    assert any("rekursi" in e for e in report["errors"])
    assert any("tak-ada" in w for w in report["warnings"])
