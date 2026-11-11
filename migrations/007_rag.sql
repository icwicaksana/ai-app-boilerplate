-- Migrasi 007: skema RAG v0.7 (desain beku: Docs/RAG-V07-DESIGN.md D3).
--
-- Aturan eksekusi (D18): via `app/infra/rag_migrate.py` (advisory lock +
-- tabel schema_migrations). JANGAN dijalankan bare psql di prod tanpa lock.
-- DILARANG `CREATE INDEX CONCURRENTLY` di sini (chunks selalu kosong saat
-- migrasi; CONCURRENTLY tak bisa dalam blok transaksi migrasi).
--
-- Catatan peran (keputusan implementasi 2026-11-11, melengkapi D2/T04):
-- 1) RLS FORCED di 5 tabel. Bootstrap user dev (superuser) MELEWATI RLS
--    (perilaku Postgres: superuser bypass RLS) — RLS adalah lapis pertahanan
--    untuk role least-privilege (prod) + diverifikasi test via `SET ROLE app_api`.
-- 2) Lease lintas-tenant HANYA via fungsi SECURITY DEFINER `claim_ingest_job`
--    (owner: rag_admin BYPASSRLS). Selain itu semua query WAJIB SET LOCAL tenant.
-- 3) Secret konektor HANYA via `get_source_secret` (SECURITY DEFINER + audit log
--    di sisi worker). Role app_api dilarang SELECT kolom secret_enc langsung
--    (dicabut via column privilege).
--
-- Urutan rollback (darurat): DROP FUNCTION claim_ingest_job; DROP FUNCTION
-- get_source_secret; DROP TABLE chunks, ingestion_jobs, documents,
-- source_watermarks, source_connections; DELETE FROM schema_migrations
-- WHERE version='007'; (role dibiarkan — harmless).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Peran least-privilege (prod) + owner fungsi privileged. Idempotent.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
        CREATE ROLE app_api NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_admin') THEN
        CREATE ROLE rag_admin NOLOGIN BYPASSRLS;
    END IF;
END
$$;

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN
        ('pdf', 'docx', 'pptx', 'xlsx', 'md', 'txt', 'pg_row', 'mysql_row')),
    source_uri TEXT NOT NULL,
    title TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'id',
    bytes BIGINT NOT NULL CHECK (bytes >= 0),
    sha256 TEXT NOT NULL,
    parser TEXT NOT NULL DEFAULT 'auto',
    chunk_preset TEXT NOT NULL DEFAULT 'prose'
        CHECK (chunk_preset IN ('prose', 'markdown', 'pdf', 'factoid')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
        ('pending', 'parsing', 'chunking', 'embedding', 'indexing',
         'ready', 'failed', 'deleted')),
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL DEFAULT '-',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant, sha256)
);
CREATE INDEX idx_documents_tenant_status
    ON documents (tenant, status, created_at DESC);
CREATE INDEX idx_documents_tenant_updated
    ON documents (tenant, updated_at DESC);

CREATE TABLE ingestion_jobs (
    id BIGSERIAL PRIMARY KEY,
    tenant TEXT NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN
        ('queued', 'leased', 'parsing', 'chunking', 'embedding',
         'indexing', 'done', 'failed', 'dead')),
    stage TEXT NOT NULL DEFAULT 'queued',
    progress SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    attempts SMALLINT NOT NULL DEFAULT 0,
    max_attempts SMALLINT NOT NULL DEFAULT 5,
    next_retry_at TIMESTAMPTZ,
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    lease_until TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_jobs_document ON ingestion_jobs (document_id);
CREATE INDEX idx_jobs_poll ON ingestion_jobs (next_retry_at, id)
    WHERE state IN ('queued', 'leased')
       OR (state = 'failed' AND next_retry_at IS NOT NULL);
CREATE INDEX idx_jobs_tenant_state
    ON ingestion_jobs (tenant, state, updated_at DESC);

CREATE TABLE chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant TEXT NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL CHECK (ord >= 0),
    content TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 8000),
    content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    token_count SMALLINT NOT NULL CHECK (token_count > 0),
    page INTEGER,
    sheet TEXT,
    heading TEXT,
    embedding VECTOR(1024),
    embedding_model TEXT NOT NULL DEFAULT 'BAAI/bge-m3',
    embedding_dim INT NOT NULL DEFAULT 1024,
    embedding_version TEXT NOT NULL DEFAULT 'v1',
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ord)
);
CREATE INDEX idx_chunks_document ON chunks (document_id);
CREATE INDEX idx_chunks_tenant_doc ON chunks (tenant, document_id, ord);
CREATE INDEX idx_chunks_tsv ON chunks USING GIN (content_tsv);
CREATE INDEX idx_chunks_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE source_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant TEXT NOT NULL,
    name TEXT NOT NULL,
    engine TEXT NOT NULL CHECK (engine IN ('postgres', 'mysql')),
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    dbname TEXT NOT NULL,
    username TEXT NOT NULL,
    secret_enc BYTEA NOT NULL,
    read_only BOOLEAN NOT NULL DEFAULT TRUE,
    allowed_tables TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant, name)
);
CREATE INDEX idx_src_conn_tenant ON source_connections (tenant, name);

CREATE TABLE source_watermarks (
    tenant TEXT NOT NULL,
    connection_id UUID NOT NULL REFERENCES source_connections(id) ON DELETE CASCADE,
    table_name TEXT NOT NULL,
    pk_last TEXT NOT NULL DEFAULT '',
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, connection_id, table_name)
);

-- Autovacuum agresif (T12): tabel tulis-berat.
ALTER TABLE chunks SET (autovacuum_vacuum_scale_factor = 0.05);
ALTER TABLE ingestion_jobs SET (autovacuum_vacuum_scale_factor = 0.025);

-- RLS FORCED + policy tenant (D2 lapis 3).
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE source_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_connections FORCE ROW LEVEL SECURITY;
ALTER TABLE source_watermarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_watermarks FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON documents
    USING (tenant = current_setting('app.current_tenant', true));
CREATE POLICY tenant_isolation ON ingestion_jobs
    USING (tenant = current_setting('app.current_tenant', true));
CREATE POLICY tenant_isolation ON chunks
    USING (tenant = current_setting('app.current_tenant', true));
CREATE POLICY tenant_isolation ON source_connections
    USING (tenant = current_setting('app.current_tenant', true));
CREATE POLICY tenant_isolation ON source_watermarks
    USING (tenant = current_setting('app.current_tenant', true));

-- Hak role least-privilege.
GRANT SELECT, INSERT, UPDATE, DELETE ON documents, ingestion_jobs, chunks,
    source_connections, source_watermarks TO app_api;
GRANT USAGE, SELECT ON SEQUENCE ingestion_jobs_id_seq TO app_api;
-- app_api dilarang baca secret_enc langsung (hanya via fungsi audited).
REVOKE ALL (secret_enc) ON source_connections FROM app_api;

-- Lease atomik lintas-tenant (T06/D12). Satu-satunya bypass RLS yang sah.
CREATE OR REPLACE FUNCTION claim_ingest_job(p_worker TEXT)
RETURNS TABLE (job_id BIGINT, job_tenant TEXT, document_id UUID, attempts SMALLINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET row_security = OFF
AS $func$
DECLARE
    r RECORD;
BEGIN
    SELECT j.id, j.tenant, j.document_id, j.attempts INTO r
    FROM ingestion_jobs j
    WHERE j.state = 'queued'
       OR (j.state = 'failed' AND j.next_retry_at <= now())
       OR (j.state = 'leased' AND j.lease_until <= now())
    ORDER BY j.next_retry_at NULLS FIRST, j.id
    LIMIT 1
    FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    UPDATE ingestion_jobs
    SET state = 'leased', stage = 'leased', claimed_by = p_worker,
        claimed_at = now(), lease_until = now() + make_interval(secs => 120),
        attempts = r.attempts + 1, updated_at = now()
    WHERE id = r.id;
    job_id := r.id;
    job_tenant := r.tenant;
    document_id := r.document_id;
    attempts := r.attempts + 1;
    RETURN NEXT;
END
$func$;
ALTER FUNCTION claim_ingest_job(TEXT) OWNER TO rag_admin;
GRANT EXECUTE ON FUNCTION claim_ingest_job(TEXT) TO app_api;

-- Akses secret konektor teraudit (T13). Worker WAJIB log setiap pemanggilan.
CREATE OR REPLACE FUNCTION get_source_secret(p_conn_id UUID)
RETURNS BYTEA
LANGUAGE sql
SECURITY DEFINER
SET row_security = OFF
AS $func$
    SELECT secret_enc FROM source_connections WHERE id = p_conn_id;
$func$;
ALTER FUNCTION get_source_secret(UUID) OWNER TO rag_admin;
GRANT EXECUTE ON FUNCTION get_source_secret(UUID) TO app_api;
