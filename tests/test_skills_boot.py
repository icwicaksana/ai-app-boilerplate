"""v0.9 F2: loader, registry, boot, dan blok health skills.

Semua akar direktori adalah fixture yang DI-COMMIT
(`tests/fixtures/skills_boot/`), bukan direktori runtime — `os.mkdir`
diblokir sandbox pada mesin pengembangan ini (lihat tests/test_skills_core.py).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.skills.boot import SkillStartupError, build_registry, resolve_root
from app.skills.loader import (
    discover,
    load_skills,
    load_supporting_files,
)
from app.skills.registry import SkillRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "skills_boot"
GOOD = FIXTURES / "good"
MIXED = FIXTURES / "mixed"


def _settings(**kw) -> Settings:
    base = dict(_env_file=None, JWT_SECRET="x" * 40,
                JWT_ISSUER="ai-app-boilerplate")
    base.update(kw)
    return Settings(**base)


# --- discover ---------------------------------------------------------------

def test_discover_abaikan_underscore():
    names = [p.name for p in discover(GOOD)]
    assert names == ["alpha", "zebra"]
    assert "_template" not in names


def test_discover_terurut_alfabetis():
    names = [p.name for p in discover(MIXED)]
    assert names == sorted(names)


def test_discover_root_tidak_ada_kosong():
    assert discover(FIXTURES / "tidak-ada") == []


# --- load_skills ------------------------------------------------------------

def test_load_good_dua_skill_nol_error():
    skills, errors = load_skills(GOOD)
    assert [s.name for s in skills] == ["alpha", "zebra"]
    assert errors == []


def test_load_mixed_satu_lolos_tiga_error():
    """Satu skill rusak tidak boleh menenggelamkan yang lain."""
    skills, errors = load_skills(MIXED)
    assert [s.name for s in skills] == ["ok-skill"]
    assert len(errors) == 3
    blob = "\n".join(errors)
    assert "broken" in blob          # YAML rusak
    assert "no-file" in blob         # tanpa SKILL.md
    assert "mismatch" in blob        # E08: nama != folder


def test_load_tidak_pernah_raise_untuk_skill_rusak():
    skills, errors = load_skills(MIXED)
    assert skills and errors  # kembali normal, bukan exception


# --- load_supporting_files (L3) ----------------------------------------------

def test_supporting_files_hanya_l3_terurut():
    files = load_supporting_files(GOOD, "alpha")
    assert files == ["references/detail.md"]
    assert "SKILL.md" not in files


def test_supporting_files_skill_tidak_ada_kosong():
    assert load_supporting_files(GOOD, "tidak-ada") == []


# --- registry -----------------------------------------------------------------

def test_registry_selalu_terurut():
    skills, _ = load_skills(GOOD)
    reg = SkillRegistry.build(list(reversed(skills)))
    assert reg.names() == ("alpha", "zebra")


def test_registry_akses():
    skills, _ = load_skills(GOOD)
    reg = SkillRegistry.build(skills)
    assert reg.get("alpha") is not None
    assert reg.get("tidak-ada") is None
    assert "alpha" in reg
    assert "tidak-ada" not in reg
    assert len(reg) == 2


def test_registry_kosong_falsy():
    assert not SkillRegistry.empty()
    assert len(SkillRegistry.empty()) == 0


def test_registry_manifest_stabil_antar_urutan():
    skills, _ = load_skills(GOOD)
    a = SkillRegistry.build(skills)
    b = SkillRegistry.build(list(reversed(skills)))
    assert a.manifest_id == b.manifest_id


def test_registry_index_deterministik():
    skills, _ = load_skills(GOOD)
    reg = SkillRegistry.build(skills)
    assert reg.index() == reg.index()
    assert reg.index().index("alpha") < reg.index().index("zebra")


# --- boot -----------------------------------------------------------------------

def test_dimatikan_hasilkan_registry_kosong():
    """SKILLS_ENABLED=false = keadaan normal, bukan error."""
    reg = build_registry(_settings(SKILLS_ENABLED=False), root=GOOD)
    assert len(reg) == 0
    assert not reg


def test_dinyalakan_muat_dari_root():
    reg = build_registry(_settings(SKILLS_ENABLED=True), root=GOOD)
    assert reg.names() == ("alpha", "zebra")
    assert reg.errors == ()
    assert reg.root == str(GOOD)


def test_strict_gagal_boot_dan_sebutkan_semua_yang_rusak():
    with pytest.raises(SkillStartupError) as exc:
        build_registry(_settings(SKILLS_ENABLED=True, SKILLS_STRICT=True),
                       root=MIXED)
    pesan = str(exc.value)
    assert "broken" in pesan
    assert "no-file" in pesan
    assert "mismatch" in pesan


def test_non_strict_tetap_boot_dengan_error_terkumpul():
    reg = build_registry(_settings(SKILLS_ENABLED=True, SKILLS_STRICT=False),
                         root=MIXED)
    assert reg.names() == ("ok-skill",)
    assert len(reg.errors) == 3


def test_direktori_tidak_ada_bukan_fatal():
    """Deployment baru boleh menyala dulu; skill menyusul."""
    reg = build_registry(_settings(SKILLS_ENABLED=True),
                         root=FIXTURES / "tidak-ada")
    assert len(reg) == 0
    assert len(reg.errors) == 1
    assert "tidak ada" in reg.errors[0]


def test_resolve_root_absolut_lewat_apa_adanya():
    abs_root = (FIXTURES / "good").resolve()
    out = resolve_root(_settings(SKILLS_DIR=str(abs_root)))
    assert out == abs_root


def test_resolve_root_relatif_dari_akar_repo_bukan_cwd():
    """Relatif dihitung dari akar repo — cwd pemanggil tidak relevan."""
    out = resolve_root(_settings(SKILLS_DIR="app/skills"))
    assert out.is_absolute()
    assert out.name == "skills"
    assert out.parent.name == "app"


# --- health ---------------------------------------------------------------------

def _request_with(skills):
    return SimpleNamespace(app=SimpleNamespace(
        state=SimpleNamespace(skills=skills)))


def test_health_dimatikan():
    from app.api.v1.routes.health import _skills_status

    req = _request_with(None)
    out = _skills_status(req, _settings(SKILLS_ENABLED=False))
    assert out.startswith("disabled")


def test_health_ok():
    from app.api.v1.routes.health import _skills_status

    skills, _ = load_skills(GOOD)
    reg = SkillRegistry.build(skills)
    out = _skills_status(_request_with(reg), _settings(SKILLS_ENABLED=True))
    assert out.startswith("ok (2 skill")
    assert "manifest=" in out


def test_health_degraded_saat_ada_error():
    from app.api.v1.routes.health import _skills_status

    reg = build_registry(_settings(SKILLS_ENABLED=True, SKILLS_STRICT=False),
                         root=MIXED)
    out = _skills_status(_request_with(reg), _settings(SKILLS_ENABLED=True))
    assert out.startswith("degraded (1 skill, 3 gagal")


def test_health_tanpa_registry():
    from app.api.v1.routes.health import _skills_status

    out = _skills_status(_request_with(None), _settings(SKILLS_ENABLED=True))
    assert out.startswith("not initialized")


def test_health_tanpa_skill():
    from app.api.v1.routes.health import _skills_status

    out = _skills_status(_request_with(SkillRegistry.empty()),
                         _settings(SKILLS_ENABLED=True))
    assert out == "enabled (tanpa skill)"


def test_health_tidak_pernah_raise():
    from app.api.v1.routes.health import _skills_status

    out = _skills_status(object(), _settings(SKILLS_ENABLED=True))
    assert out.startswith("down (")
