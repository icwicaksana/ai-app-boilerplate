-- 014_rename_tenant_to_workspace.rollback.sql (ROLLBACK migrasi 014).
--
-- SENGAJA TIDAK terdaftar di MIGRATIONS -> runner tidak pernah mengeksekusinya.
-- Jalankan manual, satu blok transaksi, HANYA bersama deploy kode LAMA:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/014_rename_tenant_to_workspace.rollback.sql
--
-- Kapan ini perlu: hanya untuk "batal semantik" (keputusan diubah). Kalau 014
-- gagal/crash, TIDAK perlu apa-apa: runner membungkus satu berkas dalam satu
-- transaksi dan baris schema_migrations ikut rollback.
--
-- Idempoten seperti versi UP: guard per-tabel, no-op bila keadaan sudah lama.
-- Mirror urutan UP: kolom -> constraint -> index -> policy -> fungsi -> versi.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '180s';

-- 1) KOLOM: workspace -> tenant (hanya bila workspace ada & tenant belum ada).
DO $do$
DECLARE
    r RECORD;
    v_ws     INT;
    v_tenant INT;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('documents'), ('ingestion_jobs'), ('chunks'),
            ('source_connections'), ('source_watermarks'),
            ('kg_entities'), ('kg_edges'), ('kg_aliases'),
            ('approvals'), ('audit_log'), ('app_users')
        ) AS v(tbl)
    LOOP
        IF to_regclass(format('public.%I', r.tbl)) IS NULL THEN
            CONTINUE;
        END IF;
        SELECT count(*) FILTER (WHERE a.attname = 'workspace'),
               count(*) FILTER (WHERE a.attname = 'tenant')
          INTO v_ws, v_tenant
          FROM pg_attribute a
         WHERE a.attrelid = format('public.%I', r.tbl)::regclass
           AND a.attnum > 0
           AND NOT a.attisdropped;
        IF v_ws = 0 THEN
            CONTINUE;                    -- sudah tenant / bukan milik 014
        ELSIF v_tenant = 1 THEN
            RAISE EXCEPTION
                '014 rollback: % punya workspace DAN tenant — drift', r.tbl;
        END IF;
        EXECUTE format('ALTER TABLE %I RENAME COLUMN workspace TO tenant', r.tbl);
        RAISE NOTICE '014 rollback: %.workspace -> %.tenant', r.tbl, r.tbl;
    END LOOP;
END
$do$;

-- 2) CONSTRAINT kembali ke nama lama (index miliknya ikut rename).
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('documents',          'documents_workspace_sha256_key',
                                   'documents_tenant_sha256_key'),
            ('source_connections', 'source_connections_workspace_name_key',
                                   'source_connections_tenant_name_key'),
            ('app_users',          'app_users_workspace_username_key',
                                   'app_users_tenant_username_key')
        ) AS v(tbl, old_name, new_name)
    LOOP
        IF to_regclass(format('public.%I', r.tbl)) IS NULL THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
             WHERE c.conrelid = format('public.%I', r.tbl)::regclass
               AND c.conname = r.old_name
        ) THEN
            CONTINUE;
        END IF;
        EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                       r.tbl, r.old_name, r.new_name);
        RAISE NOTICE '014 rollback: constraint % -> %', r.old_name, r.new_name;
    END LOOP;
END
$do$;

-- 3) INDEX kembali ke nama lama.
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('idx_documents_workspace_status',  'idx_documents_tenant_status'),
            ('idx_documents_workspace_updated', 'idx_documents_tenant_updated'),
            ('idx_jobs_workspace_state',        'idx_jobs_tenant_state'),
            ('idx_chunks_workspace_doc',        'idx_chunks_tenant_doc'),
            ('idx_src_conn_workspace',          'idx_src_conn_tenant'),
            ('audit_workspace_idx',             'audit_tenant_idx')
        ) AS v(old_name, new_name)
    LOOP
        IF to_regclass(format('public.%I', r.old_name)) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.old_name, r.new_name);
        RAISE NOTICE '014 rollback: index % -> %', r.old_name, r.new_name;
    END LOOP;
END
$do$;

-- 4) POLICY: DROP + CREATE dengan kolom `tenant` (USING + WITH CHECK,
--    GUC tetap app.current_tenant). Dijalankan SETELAH rename kolom supaya
--    `tenant` pasti ada saat CREATE POLICY di-parse.
DROP POLICY IF EXISTS tenant_isolation ON documents;
CREATE POLICY tenant_isolation ON documents
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON ingestion_jobs;
CREATE POLICY tenant_isolation ON ingestion_jobs
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON chunks;
CREATE POLICY tenant_isolation ON chunks
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON source_connections;
CREATE POLICY tenant_isolation ON source_connections
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON source_watermarks;
CREATE POLICY tenant_isolation ON source_watermarks
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON kg_entities;
CREATE POLICY tenant_isolation ON kg_entities
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON kg_edges;
CREATE POLICY tenant_isolation ON kg_edges
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON kg_aliases;
CREATE POLICY tenant_isolation ON kg_aliases
    USING (tenant = current_setting('app.current_tenant', true))
    WITH CHECK (tenant = current_setting('app.current_tenant', true));

-- 5) FUNGSI: claim_ingest_job kembali membaca `tenant`
--    (signature & RETURNS TABLE identik dengan 009/014).
CREATE OR REPLACE FUNCTION claim_ingest_job(p_worker TEXT,
                                            p_lease_secs INT DEFAULT 120)
RETURNS TABLE (job_id BIGINT, job_tenant TEXT, document_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
DECLARE
    v_id     BIGINT;
    v_tenant TEXT;
    v_doc    UUID;
BEGIN
    UPDATE ingestion_jobs j SET state = 'dead',
        last_error = COALESCE(j.last_error, 'POISON_PILL')
            || ' | attempts habis tanpa terminal',
        updated_at = now()
    WHERE j.state NOT IN ('done', 'dead')
      AND j.attempts >= j.max_attempts;

    SELECT j.id, j.tenant, j.document_id
      INTO v_id, v_tenant, v_doc
      FROM ingestion_jobs j
     WHERE (j.state = 'queued'
            OR (j.state = 'failed' AND j.next_retry_at <= now())
            OR (j.state NOT IN ('done', 'dead', 'queued')
                AND j.lease_until <= now()))
       AND j.attempts < j.max_attempts
     ORDER BY j.next_retry_at NULLS FIRST, j.id
     LIMIT 1
     FOR UPDATE SKIP LOCKED;

    IF v_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE ingestion_jobs
       SET state = 'leased',
           claimed_by = p_worker,
           claimed_at = now(),
           lease_until = now() + make_interval(secs => GREATEST(p_lease_secs, 10)),
           attempts = attempts + 1,
           updated_at = now()
     WHERE id = v_id;

    job_id := v_id;
    job_tenant := v_tenant;
    document_id := v_doc;
    RETURN NEXT;
END;
$func$;

ALTER FUNCTION claim_ingest_job(TEXT, INT) OWNER TO rag_admin;
GRANT EXECUTE ON FUNCTION claim_ingest_job(TEXT, INT) TO app_api;
ALTER FUNCTION get_source_secret(UUID) OWNER TO rag_admin;
ALTER FUNCTION get_source_secret(UUID) SET search_path = public;
GRANT EXECUTE ON FUNCTION get_source_secret(UUID) TO app_api;

-- 6) Lupakan versi 014 supaya bisa diterapkan ulang setelah kode lama jalan.
--    (Runner tidak melakukan ini untuk berkas rollback manual.)
DELETE FROM schema_migrations WHERE version = '014';

DO $do$
BEGIN
    RAISE NOTICE '014 rollback: selesai — JANGAN lupa deploy kode lama + '
                 'hapus "014" dari MIGRATIONS di app/infra/rag_migrate.py';
END
$do$;

COMMIT;
