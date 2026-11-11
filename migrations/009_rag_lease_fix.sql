-- Migrasi 009: perbaiki state machine lease (temuan review F8: K1, K3, P1-6).
--
-- Masalah: `set_stage()` menimpa `state` dengan nama stage (parsing/...),
-- sehingga (a) heartbeat `WHERE state='leased'` selalu gagal, (b) job macet
-- di stage tak pernah direklamasi `claim_ingest_job`, (c) klaim tanpa batas
-- attempts (poison-pill loop), (d) lease 120 dtk hardcode.
--
-- Perbaikan:
-- 1. `state` HANYA lifecycle (queued/leased/failed/done/dead); `stage` kolom
--    terpisah untuk progres. Normalisasi baris macet di bawah.
-- 2. `claim_ingest_job(p_worker, p_lease_secs)` — reklamasi SEMUA non-terminal
--    kedaluwarsa + batas attempts + sweep ke dead bila habis.
-- Idempotent: CREATE OR REPLACE + UPDATE bersyarat.
-- ATAU no-op bila rollback parsial: guard IF EXISTS tidak untuk fungsi —
-- CREATE OR REPLACE selalu aman diulang.

-- 1) Normalisasi baris macet (state=stage): masih dalam lease → leased,
--    selebihnya → queued (diklaim ulang dengan attempts yang tersisa).
UPDATE ingestion_jobs
SET state = CASE WHEN lease_until > now() THEN 'leased' ELSE 'queued' END,
    updated_at = now()
WHERE state IN ('parsing', 'chunking', 'embedding', 'indexing');

-- Index klaim (poll 0.5 dtk/worker + sweep attempts; tanpa ini full scan).
CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON ingestion_jobs (state, next_retry_at, id);
CREATE INDEX IF NOT EXISTS idx_jobs_sweep
    ON ingestion_jobs (attempts, max_attempts)
    WHERE state NOT IN ('done', 'dead');

-- Hapus overload lama (1 arg) — CREATE OR REPLACE dengan signatur beda TIDAK
-- menimpanya (overload), harus DROP eksplisit.
DROP FUNCTION IF EXISTS claim_ingest_job(TEXT);

CREATE OR REPLACE FUNCTION claim_ingest_job(p_worker TEXT,
                                            p_lease_secs INT DEFAULT 120)
RETURNS TABLE (job_id BIGINT, job_tenant TEXT, document_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_id BIGINT;
    v_tenant TEXT;
    v_doc UUID;
BEGIN
    -- Sweep: non-terminal yang attempts-nya habis → dead (anti poison-pill).
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
$$;

ALTER FUNCTION claim_ingest_job(TEXT, INT) OWNER TO rag_admin;
