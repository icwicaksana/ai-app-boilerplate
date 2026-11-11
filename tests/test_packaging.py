"""Guard packaging (F7): Dockerfile & .dockerignore tak boleh regresi.

Dua bug nyata yang ditemukan saat verifikasi image:
1. `COPY migrations` HILANG → container gagal start ("007_rag.sql tidak
   ditemukan") karena `rag_migrate` membaca `<repo>/migrations`.
2. `.dockerignore` HILANG → build context menarik `.venv` (1.6GB) +
   `.hf-cache` (0.5GB) + `data/` ≈ 2.1GB (vs 1.2MB kode); transfer lama dan
   daemon Docker bisa wedge saat unpack.

Test ini murah, tanpa Docker, dan mengunci kedua perbaikan.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def _lines() -> list[str]:
    return [ln.strip() for ln in
            DOCKERFILE.read_text(encoding="utf-8").splitlines()]


def test_dockerfile_menyalin_migrasi():
    """Migrasi WAJIB ada di image (SoR skema RAG)."""
    assert DOCKERFILE.is_file()
    joined = "\n".join(_lines())
    assert "COPY migrations ./migrations" in joined, (
        "Dockerfile wajib COPY migrations — rag_migrate membacanya saat start")


def test_dockerfile_entrypoint_kanonis():
    """`python -m app.main` (loop selector), BUKAN flag CLI --loop."""
    # Hanya baris PERINTAH (komentar boleh menyebut --loop sebagai penjelasan).
    perintah = [ln for ln in _lines() if ln and not ln.startswith("#")]
    joined = "\n".join(perintah)
    assert 'CMD ["python", "-m", "app.main"]' in joined
    assert "--loop" not in joined, (
        "flag CLI --loop menolak nilai selector (temuan F-01)")


def test_dockerfile_non_root_dan_dir_runtime():
    joined = "\n".join(_lines())
    assert "USER appuser" in joined, "image wajib non-root"
    assert "RAG_STORAGE_DIR=/data/uploads" in joined
    assert "RAG_HF_CACHE=/cache/hf" in joined


def test_dockerignore_menutup_artefak_besar():
    assert DOCKERIGNORE.is_file(), ".dockerignore wajib ada (context 2.1GB)"
    entries = {ln.strip().rstrip("/") for ln in
               DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.strip().startswith("#")}
    for wajib in (".venv", ".hf-cache", "data", ".env", "tests", ".git",
                  "__pycache__", ".pytest_cache"):
        assert wajib in entries, f".dockerignore kehilangan: {wajib}"


def test_dockerignore_tidak_menutup_kebutuhan_image():
    """Yang dipakai runtime TIDAK boleh di-ignore."""
    entries = {ln.strip().rstrip("/") for ln in
               DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.strip().startswith("#")}
    for penting in ("app", "migrations", "requirements.lock"):
        assert penting not in entries, f"jangan ignore: {penting}"
