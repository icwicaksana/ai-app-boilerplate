"""v0.9 F1: core skill murni — parser, validasi, indeks, sidik jari.

Berkas skill NYATA dibaca dari `tests/fixtures/skills/` yang di-commit, bukan
string yang dikarang di memori. Alasannya konkret: di F0 dua kali tes
menangkap kesalahan yang tidak terlihat dari membaca kode. Parser yang diuji
dengan string karangan hanya membuktikan parser setuju dengan anggapan
penulisnya.

Catatan lingkungan: `tmp_path` bawaan pytest TIDAK dipakai karena membuat
direktori di temp sistem, dan `os.mkdir` diblokir sandbox Windows pada mesin
pengembangan ini. Fixture yang di-commit lebih baik juga untuk CI: berkas
yang sama diuji di mana pun.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.skills.hash import content_hash, manifest_id
from app.skills.index import build_index
from app.skills.model import SkillParseError, SkillValidationError
from app.skills.parser import parse_skill
from app.skills.validate import validate_folder_name


FIXTURE = Path(__file__).parent / "fixtures" / "skills"


def _parse_file(path: Path):
    return parse_skill(path.read_text(encoding="utf-8"), source=path.name)



def _parse_file(path):
    return parse_skill(path.read_text(encoding="utf-8"),
                       source=str(path.name))


# --- parsing normal --------------------------------------------------------

def test_parse_berkas_nyata():
    """Berkas .md SUNGGUHAN dari disk, bukan string karangan."""
    path = FIXTURE / "sop-garansi" / "SKILL.md"
    skill = _parse_file(path)
    assert skill.name == "sop-garansi"
    assert skill.tags == ("garansi", "klaim")
    assert "12 bulan" in skill.body
    assert skill.content_hash


def test_folder_harus_sama_dengan_nama():
    path = FIXTURE / "sop-garansi" / "SKILL.md"
    skill = _parse_file(path)
    validate_folder_name(skill.name, path.parent.name, source=str(path))
    with pytest.raises(SkillValidationError):
        validate_folder_name(skill.name, "folder-lain", source=str(path))


# --- kegagalan parsing -----------------------------------------------------

def test_tanpa_frontmatter_ditolak():
    with pytest.raises(SkillParseError, match="frontmatter"):
        parse_skill("# Judul saja\n\nisi")


def test_frontmatter_tidak_di_baris_pertama_ditolak():
    with pytest.raises(SkillParseError, match="frontmatter"):
        parse_skill("\n---\nname: x\n---\n\nisi")


def test_yaml_rusak_ditolak():
    with pytest.raises(SkillParseError, match="YAML"):
        parse_skill("---\nname: [belum ditutup\n---\n\nisi")


def test_frontmatter_bukan_pemetaan_ditolak():
    with pytest.raises(SkillParseError, match="pemetaan"):
        parse_skill("---\n- satu\n- dua\n---\n\nisi")


def test_berkas_kosong_ditolak():
    with pytest.raises(SkillParseError, match="kosong"):
        parse_skill("   \n\n")


# --- validasi E01-E08 ------------------------------------------------------

def _meta(**kw):
    base = {
        "name": "sop-garansi",
        "description": "Prosedur klaim garansi 12 bulan dan SLA 3 hari",
        "when_to_use": "user menanyakan garansi",
        "tags": ["garansi"],
    }
    base.update(kw)
    return base


def test_field_tak_dikenal_ditolak():
    """E07: typo seperti `descripion` tidak boleh lolos diam-diam."""
    with pytest.raises(SkillValidationError, match="tak dikenal"):
        parse_skill("---\ndescripion: salah\n---\n\nisi")


@pytest.mark.parametrize("nama", ["SOP", "sop garansi", "-sop", "a", "../x"])
def test_nama_tidak_sah_ditolak(nama):
    """E01: nama jadi argumen tool — `../x` akan jadi path traversal di L3."""
    with pytest.raises(SkillValidationError, match="name"):
        parse_skill(f"---\nname: '{nama}'\ndescription: cukup panjang sekali\n"
                    f"when_to_use: kapan saja\n---\n\nisi")


def test_description_terlalu_pendek_ditolak():
    with pytest.raises(SkillValidationError, match="description"):
        parse_skill("---\nname: sop-a\ndescription: pendek\n"
                    "when_to_use: kapan saja\n---\n\nisi")


def test_description_multibaris_ditolak():
    with pytest.raises(SkillValidationError, match="satu baris"):
        parse_skill('---\nname: sop-a\ndescription: "baris satu\\nbaris dua"\n'
                    "when_to_use: kapan saja\n---\n\nisi")


def test_tag_tidak_sah_ditolak():
    with pytest.raises(SkillValidationError, match="tag"):
        parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\ntags: [Garansi]\n---\n\nisi")


def test_tag_duplikat_ditolak():
    with pytest.raises(SkillValidationError, match="duplikat"):
        parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\ntags: [a, a]\n---\n\nisi")


def test_body_kosong_ditolak():
    with pytest.raises(SkillValidationError, match="body"):
        parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\n---\n\n   ")


def test_body_terlalu_panjang_ditolak():
    with pytest.raises(SkillValidationError, match="melebihi batas"):
        parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\n---\n\n" + ("x" * 20_001))


# --- indeks byte-stabil ----------------------------------------------------

def test_indeks_byte_stabil():
    """Dua pemanggilan = string identik. Kalau tidak, prefix-cache gugur."""
    skills = [_parse_file(FIXTURE / "sop-garansi" / "SKILL.md")]
    assert build_index(skills) == build_index(skills)


def test_indeks_terurut_alfabetis():
    a = parse_skill("---\nname: zebra\ndescription: deskripsi zebra panjang\n"
                    "when_to_use: kapan zebra\n---\n\nisi")
    b = parse_skill("---\nname: alpha\ndescription: deskripsi alpha panjang\n"
                    "when_to_use: kapan alpha\n---\n\nisi")
    out = build_index([a, b])
    assert out.index("alpha") < out.index("zebra")


def test_indeks_tanpa_skill_kosong():
    assert build_index([]) == ""


def test_indeks_bisa_tanpa_when_to_use():
    skill = parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                        "when_to_use: kapan saja\n---\n\nisi")
    out = build_index([skill], include_when_to_use=False)
    assert "pakai saat" not in out
    assert out.startswith("- sop-a: ")


# --- sidik jari ------------------------------------------------------------

def test_hash_berubah_saat_body_berubah():
    a = parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\n---\n\nisi satu")
    b = parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\n---\n\nisi dua")
    assert a.content_hash != b.content_hash


def test_hash_tidak_berubah_karena_urutan_tag():
    """Tag diurutkan di dalam hash — urutan penulisan bukan perubahan isi."""
    a = parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\ntags: [b, a]\n---\n\nisi")
    b = parse_skill("---\nname: sop-a\ndescription: cukup panjang sekali\n"
                    "when_to_use: kapan saja\ntags: [a, b]\n---\n\nisi")
    assert a.content_hash == b.content_hash


def test_manifest_id_tidak_bergantung_urutan():
    a = parse_skill("---\nname: alpha\ndescription: deskripsi alpha panjang\n"
                    "when_to_use: kapan alpha\n---\n\nisi")
    b = parse_skill("---\nname: zebra\ndescription: deskripsi zebra panjang\n"
                    "when_to_use: kapan zebra\n---\n\nisi")
    assert manifest_id([a, b]) == manifest_id([b, a])


def test_manifest_id_berubah_saat_roster_berubah():
    a = parse_skill("---\nname: alpha\ndescription: deskripsi alpha panjang\n"
                    "when_to_use: kapan alpha\n---\n\nisi")
    assert manifest_id([a]) != manifest_id([])
