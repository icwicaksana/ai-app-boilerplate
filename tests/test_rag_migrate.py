"""Unit splitter migrasi (D18). Murni, tanpa DB."""

from app.infra.rag_migrate import (
    MIGRATION_FILE,
    MIGRATIONS,
    MIGRATIONS_DIR,
    split_statements,
)


def test_split_dasar_dan_komentar():
    sql = "-- komentar;\nSELECT 1; /* blok; */ SELECT 2;"
    assert split_statements(sql) == ["SELECT 1", "SELECT 2"]


def test_split_abaikan_semicolon_dalam_string():
    sql = "INSERT INTO t (a) VALUES ('a;b'); SELECT 1;"
    assert split_statements(sql) == ["INSERT INTO t (a) VALUES ('a;b')", "SELECT 1"]


def test_split_dollar_quote_fungsi():
    sql = (
        "CREATE FUNCTION f() RETURNS void AS $func$ "
        "BEGIN RAISE NOTICE 'x;y'; END $func$; SELECT 1;"
    )
    parts = split_statements(sql)
    assert len(parts) == 2
    assert parts[0].startswith("CREATE FUNCTION")
    assert parts[1] == "SELECT 1"


def test_split_escape_quote_ganda():
    sql = "SELECT 'it''s; ok'; SELECT 2;"
    assert split_statements(sql) == ["SELECT 'it''s; ok'", "SELECT 2"]


def test_berkas_007_valid_dan_lengkap():
    text = MIGRATION_FILE.read_text(encoding="utf-8")
    parts = split_statements(text)
    assert len(parts) >= 25, f"007 hanya {len(parts)} statement — ada yang hilang?"
    joined = "\n".join(parts)
    for needle in ("CREATE EXTENSION", "CREATE TABLE documents",
                   "CREATE TABLE ingestion_jobs", "CREATE TABLE chunks",
                   "source_connections", "source_watermarks",
                   "FORCE ROW LEVEL SECURITY", "claim_ingest_job",
                   "get_source_secret", "idx_chunks_hnsw"):
        assert needle in joined, f"007 kehilangan: {needle}"
    # Dilarang CONCURRENTLY dalam migrasi transaksional (D18).
    assert "CONCURRENTLY" not in joined.upper()


def test_semua_migrasi_terdaftar_valid():
    seen = set()
    for version, filename in MIGRATIONS:
        assert version not in seen, f"versi ganda: {version}"
        seen.add(version)
        path = MIGRATIONS_DIR / filename
        assert path.is_file(), f"berkas hilang: {filename}"
        parts = split_statements(path.read_text(encoding="utf-8"))
        assert parts, f"{filename} kosong"
    joined008 = (MIGRATIONS_DIR / "008_rag_grants.sql").read_text(
        encoding="utf-8")
    assert "GRANT" in joined008 and "rag_admin" in joined008
    joined009 = (MIGRATIONS_DIR / "009_rag_lease_fix.sql").read_text(
        encoding="utf-8")
    for needle in ("DROP FUNCTION IF EXISTS claim_ingest_job(TEXT)",
                   "p_lease_secs", "attempts >= j.max_attempts",
                   "idx_jobs_claim",
                   "WHERE state IN ('parsing', 'chunking'"):
        assert needle in joined009, f"009 kehilangan: {needle}"
    joined010 = (MIGRATIONS_DIR / "010_rag_norm_lexical.sql").read_text(
        encoding="utf-8")
    for needle in ("content_norm", "content_tsv_norm",
                   "idx_chunks_tsv_norm"):
        assert needle in joined010, f"010 kehilangan: {needle}"
