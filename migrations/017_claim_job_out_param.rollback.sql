-- 017_claim_job_out_param.rollback.sql (down untuk 017 — MANUAL, tidak
-- pernah didaftarkan di MIGRATIONS).
--
-- Kembalikan signature ke `job_tenant` TEPAT seperti 014, supaya kode lama
-- (app/infra/ingest_repo.py:177 `row["job_tenant"]`) jalan lagi.
--
-- Bedanya dengan keadaan 014: EXECUTE untuk PUBLIC TIDAK dikembalikan.
-- Rollback memulihkan SIGNATURE, bukan lubang keamanan. Kalau benar-benar
-- perlu (mis. ada role DB lain yang memanggil), lakukan sadar-sadar:
--     GRANT EXECUTE ON FUNCTION claim_ingest_job(TEXT,INT) TO <role>;
-- (bukan ke PUBLIC).
--
-- Jalankan dalam transaksi (runner membungkus; psql manual: BEGIN ... COMMIT).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- 1) PREFLIGHT: prasyarat yang sama dengan 017-up.
DO $do$
DECLARE
    v_owner_oid OID;
    v_owner     TEXT;
    v_dep       INT;
BEGIN
    IF to_regclass('public.ingestion_jobs') IS NULL THEN
        RAISE EXCEPTION '017-down preflight: ingestion_jobs tidak ada';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_admin') THEN
        RAISE EXCEPTION '017-down preflight: role rag_admin tidak ada';
    END IF;

    SELECT p.proowner, pg_get_userbyid(p.proowner) INTO v_owner_oid, v_owner
      FROM pg_proc p
     WHERE p.pronamespace = 'public'::regnamespace
       AND p.proname = 'claim_ingest_job'
       AND p.pronargs = 2;
    IF v_owner_oid IS NOT NULL AND NOT pg_has_role(v_owner_oid, 'MEMBER') THEN
        RAISE EXCEPTION
            '017-down preflight: fungsi dimiliki %, role migrasi bukan '
            'superuser/member-nya', v_owner;
    END IF;
    IF v_owner_oid IS NOT NULL THEN
        SELECT count(*) INTO v_dep
          FROM pg_depend d
         WHERE d.refclassid = 'pg_proc'::regclass
           AND d.refobjid = (SELECT p.oid FROM pg_proc p
                              WHERE p.pronamespace = 'public'::regnamespace
                                AND p.proname = 'claim_ingest_job'
                                AND p.pronargs = 2)
           AND d.deptype IN ('n', 'a');
        IF v_dep > 0 THEN
            RAISE EXCEPTION
                '017-down preflight: % objek bergantung pada fungsi', v_dep;
        END IF;
    END IF;
END
$do$;

-- 2) DROP dua signature (idempoten; (TEXT) tetap tak tersentuh versi 2-arg).
DROP FUNCTION IF EXISTS claim_ingest_job(TEXT);
DROP FUNCTION IF EXISTS claim_ingest_job(TEXT, INT);

-- 3) CREATE ulang versi 014 (OUT `job_tenant`).
CREATE FUNCTION claim_ingest_job(p_worker TEXT,
                                 p_lease_secs INT DEFAULT 120)
RETURNS TABLE (job_id BIGINT, job_tenant TEXT, document_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
DECLARE
    v_id        BIGINT;
    v_workspace TEXT;
    v_doc       UUID;
BEGIN
    UPDATE ingestion_jobs j SET state = 'dead',
        last_error = COALESCE(j.last_error, 'POISON_PILL')
            || ' | attempts habis tanpa terminal',
        updated_at = now()
    WHERE j.state NOT IN ('done', 'dead')
      AND j.attempts >= j.max_attempts;

    SELECT j.id, j.workspace, j.document_id
      INTO v_id, v_workspace, v_doc
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
    job_tenant := v_workspace;
    document_id := v_doc;
    RETURN NEXT;
END;
$func$;

-- 4) Ownership + grant (PUBLIC sengaja TIDAK dikembalikan).
ALTER FUNCTION claim_ingest_job(TEXT, INT) OWNER TO rag_admin;
REVOKE ALL ON FUNCTION claim_ingest_job(TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_ingest_job(TEXT, INT) TO app_api;

-- 5) VERIFIKASI
DO $do$
DECLARE
    v_n INT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
         WHERE p.pronamespace = 'public'::regnamespace
           AND p.proname = 'claim_ingest_job'
           AND p.pronargs = 2
           AND p.prosecdef
           AND 'search_path=public' = ANY (COALESCE(p.proconfig, ARRAY[]::text[]))
           AND pg_get_userbyid(p.proowner) = 'rag_admin'
           AND 'job_tenant' = ANY (COALESCE(p.proargnames, ARRAY[]::text[]))
           AND NOT ('job_workspace' = ANY (COALESCE(p.proargnames, ARRAY[]::text[])))
    ) THEN
        RAISE EXCEPTION '017-down verify: signature job_tenant tidak kembali';
    END IF;
    IF NOT has_function_privilege('app_api',
                                 'public.claim_ingest_job(text,integer)',
                                 'EXECUTE') THEN
        RAISE EXCEPTION '017-down verify: app_api kehilangan EXECUTE';
    END IF;
    SELECT count(*) INTO v_n FROM pg_proc p
     WHERE p.pronamespace = 'public'::regnamespace
       AND p.proname = 'claim_ingest_job';
    IF v_n <> 1 THEN
        RAISE EXCEPTION '017-down verify: % versi fungsi (harus 1)', v_n;
    END IF;
    RAISE NOTICE '017-down: claim_ingest_job kembali ke OUT job_tenant';
END
$do$;

-- Kode aplikasi harus ikut di-rollback (`row["job_workspace"]` ->
-- `row["job_tenant"]`) di deploy yang sama, kalau tidak worker crash KeyError.
-- Untuk membuat runner menjalankan 017 lagi:
--   DELETE FROM schema_migrations WHERE version = '017';
