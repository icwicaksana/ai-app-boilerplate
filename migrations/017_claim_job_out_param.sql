-- 017_claim_job_out_param.sql (v0.8.2, Tier C #2): OUT param `job_tenant`
-- -> `job_workspace` pada fungsi SECURITY DEFINER `claim_ingest_job`.
--
-- RUANG LINGKUP: signature fungsi saja (nama kolom OUT). Body, semantics,
-- lease, sweep poison-pill, dan nama argumen IN (`p_worker`, `p_lease_secs`)
-- TIDAK berubah. 016 (policy) dan 018 (data) terpisah karena blast radius-nya
-- berbeda: yang ini MENGAPUS kontrak fungsi untuk kode lama.
--
-- ---------------------------------------------------------------------------
-- KENAPA DROP + CREATE, BUKAN CREATE OR REPLACE
-- ---------------------------------------------------------------------------
-- Nama kolom OUT adalah bagian dari TIPE KEMBALIAN fungsi. PostgreSQL
-- menolak CREATE OR REPLACE yang mengubah tipe kembalian:
--     ERROR: cannot change return type of existing function
--     HINT:  Use DROP FUNCTION claim_ingest_job(text,integer) first.
-- Jadi tidak ada jalan pintas; DROP + CREATE adalah satu-satunya cara.
--
-- ---------------------------------------------------------------------------
-- URUTAN AMAN (jangan diacak)
-- ---------------------------------------------------------------------------
--   0. lock_timeout/statement_timeout  — gagal cepat, jangan tumpuk antrean
--      lock di belakang query panjang (lock queue = outage).
--   1. PREFLIGHT (tanpa DDL): tabel+kolom tujuan ada, role prasyarat ada,
--      dan TIDAK ada objek lain yang bergantung pada fungsi (kalau ada,
--      DROP gagal dengan pesan mentah "cannot drop ... because other
--      objects depend on it" — kita ingin pesan yang bisa ditindak).
--   2. DROP signature lama (dua-duanya: (TEXT) peninggalan 007 dan
--      (TEXT, INT) yang aktif). DROP mencocokkan TIPE ARGUMEN PERSIS, jadi
--      DROP (TEXT) TIDAK menyentuh (TEXT, INT) — keduanya harus eksplisit.
--      JANGAN `DROP FUNCTION claim_ingest_job` tanpa argumen: ambigu.
--   3. CREATE ulang dengan OUT `job_workspace`, SECURITY DEFINER, dan
--      `SET search_path = public` ter-pin (SECURITY DEFINER tanpa
--      search_path = kelas CVE: objek di schema lain bisa membajak resolusi
--      nama saat berjalan sebagai owner BYPASSRLS).
--   4. ALTER ... OWNER TO rag_admin  — WAJIB: DROP membuang ownership, dan
--      CREATE sebagai role migrasi menjadikan role migrasi owner. Tanpa
--      langkah ini, fungsi berhenti berjalan sebagai rag_admin (BYPASSRLS)
--      dan klaim lintas-workspace langsung rusak.
--   5. REVOKE ... FROM PUBLIC  — WAJIB setelah CREATE: PostgreSQL memberi
--      EXECUTE ke PUBLIC secara default. Untuk SECURITY DEFINER milik role
--      BYPASSRLS, PUBLIC EXECUTE = siapa pun yang bisa connect ke DB bisa
--      mengklaim job SEMUA workspace. 007 tidak pernah mencabutnya (lubang
--      yang sama kelasnya dengan temuan 015).
--   6. GRANT EXECUTE ... TO app_api — WAJIB: DROP membuang grant.
--   7. VERIFIKASI (gagal = rollback total).
--
-- Ketujuh langkah berjalan dalam SATU transaksi (runner). Tidak ada jendela
-- waktu di mana fungsi tidak ada bagi sesi lain: DDL transaksional, dan
-- pemanggil konkuren memblokir pada lock objek sampai COMMIT.
--
-- PRASYARAT ROLE: langkah 2 butuh hak MILIK atas fungsi yang saat ini
-- dimiliki `rag_admin`. Artinya role migrasi harus superuser ATAU member
-- `rag_admin`. Kalau tidak, error-nya:
--     ERROR: must be owner of function claim_ingest_job
-- (Fakta bahwa 014 bisa `ALTER FUNCTION ... OWNER TO rag_admin` membuktikan
-- role migrasi di deployment ini memang privileged — tapi jangan diasumsikan;
-- preflight di bawah memeriksanya lebih dulu.)
--
-- `SET row_security = OFF` SENGAJA TIDAK dipasang (014 membuangnya). Owner
-- fungsi punya BYPASSRLS; `row_security = off` hanya akan membuat fungsi
-- MENGERAS ("query would be affected by row-level security policy") bila
-- suatu saat owner kehilangan BYPASSRLS — lebih buruk daripada terfilter.
--
-- ROLLBACK: migrations/017_claim_job_out_param.rollback.sql

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- 1) PREFLIGHT
DO $do$
DECLARE
    v_oid    OID;
    v_dep    INT;
    v_owner  TEXT;
    v_owner_oid OID;
BEGIN
    IF to_regclass('public.ingestion_jobs') IS NULL THEN
        RAISE EXCEPTION '017 preflight: ingestion_jobs tidak ada (007 belum jalan)';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
         WHERE a.attrelid = 'public.ingestion_jobs'::regclass
           AND a.attname = 'workspace'
           AND a.attnum > 0 AND NOT a.attisdropped
    ) THEN
        RAISE EXCEPTION '017 preflight: ingestion_jobs belum punya kolom workspace '
                        '(014 belum jalan)';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
        RAISE EXCEPTION '017 preflight: role app_api tidak ada (007 belum jalan)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_admin') THEN
        RAISE EXCEPTION '017 preflight: role rag_admin tidak ada (007 belum jalan)';
    END IF;

    -- Hak milik atas versi yang sekarang (kalau ada) — biar gagal di sini,
    -- bukan di tengah DROP dengan pesan mentah.
    SELECT p.oid, p.proowner, pg_get_userbyid(p.proowner)
      INTO v_oid, v_owner_oid, v_owner
      FROM pg_proc p
     WHERE p.pronamespace = 'public'::regnamespace
       AND p.proname = 'claim_ingest_job'
       AND p.pronargs = 2;
    IF v_oid IS NOT NULL THEN
        -- 2-arg form = "apakah current_user member role ini" (tanpa ambiguitas
        -- overload name/oid).
        IF NOT pg_has_role(v_owner_oid, 'MEMBER') THEN
            RAISE EXCEPTION
                '017 preflight: fungsi dimiliki %, role migrasi % bukan '
                'superuser/member-nya — DROP akan gagal "must be owner"',
                v_owner, current_user;
        END IF;
        -- Dependen (view / policy / fungsi lain) akan memblokir DROP.
        SELECT count(*) INTO v_dep
          FROM pg_depend d
         WHERE d.refclassid = 'pg_proc'::regclass
           AND d.refobjid = v_oid
           AND d.deptype IN ('n', 'a');
        IF v_dep > 0 THEN
            RAISE EXCEPTION
                '017 preflight: % objek bergantung pada claim_ingest_job — '
                'DROP akan gagal; lepaskan dulu (tanpa CASCADE)', v_dep;
        END IF;
    END IF;
END
$do$;

-- 2) DROP signature lama. Dua statement, sengaja: (TEXT) adalah sisa 007
--    (sudah dibuang 009) yang bisa hidup lagi di DB hasil restore lama.
DROP FUNCTION IF EXISTS claim_ingest_job(TEXT);
DROP FUNCTION IF EXISTS claim_ingest_job(TEXT, INT);

-- 3) CREATE ulang. Body IDENTIK dengan 014 (state yang benar sekarang);
--    satu-satunya perubahan: `job_tenant` -> `job_workspace`.
--    Semua rujukan kolom tetap DI-QUALIFY alias `j` — nama OUT param adalah
--    variabel PL/pgSQL, jadi rujukan `workspace` tanpa alias bisa
--    "column reference is ambiguous" (default plpgsql.variable_conflict=error).
CREATE FUNCTION claim_ingest_job(p_worker TEXT,
                                 p_lease_secs INT DEFAULT 120)
RETURNS TABLE (job_id BIGINT, job_workspace TEXT, document_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
DECLARE
    v_id        BIGINT;
    v_workspace TEXT;
    v_doc       UUID;
BEGIN
    -- Sweep: non-terminal yang attempts-nya habis -> dead (anti poison-pill).
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
    job_workspace := v_workspace;
    document_id := v_doc;
    RETURN NEXT;
END;
$func$;

-- 4) Ownership (hilang karena DROP).
--    JEBAKAN TERUJI: mengubah owner menuntut OWNER BARU punya CREATE di schema
--    objeknya. Kalau `rag_admin` tidak punya CREATE di `public`, error-nya
--    menyesatkan (menunjuk schema, bukan fungsi/role):
--        ERROR: permission denied for schema public
--        STATEMENT: ALTER FUNCTION claim_ingest_job(...) OWNER TO rag_admin
--    Terjadi di setiap deployment yang migrator-nya BUKAN superuser. Cek:
--        SELECT has_schema_privilege('rag_admin', 'public', 'CREATE');
--    Kalau false: `GRANT CREATE ON SCHEMA public TO rag_admin;` (atau jalankan
--    migrasi sebagai superuser — di deployment ini memang begitu, karena 007
--    sudah menuntutnya lebih dulu).
ALTER FUNCTION claim_ingest_job(TEXT, INT) OWNER TO rag_admin;

-- 5) Cabut EXECUTE default PUBLIC (lihat alasan di header).
REVOKE ALL ON FUNCTION claim_ingest_job(TEXT, INT) FROM PUBLIC;

-- 6) Grant ulang ke role aplikasi (hilang karena DROP).
GRANT EXECUTE ON FUNCTION claim_ingest_job(TEXT, INT) TO app_api;

-- 7) VERIFIKASI
DO $do$
DECLARE
    v_n INT;
BEGIN
    -- (a) signature + hardening
    IF NOT EXISTS (
        SELECT 1
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = 'claim_ingest_job'
           AND p.pronargs = 2
           AND p.pronargdefaults = 1
           AND p.prosecdef
           AND 'search_path=public' = ANY (COALESCE(p.proconfig, ARRAY[]::text[]))
           AND pg_get_userbyid(p.proowner) = 'rag_admin'
           AND 'job_workspace' = ANY (COALESCE(p.proargnames, ARRAY[]::text[]))
           AND NOT ('job_tenant' = ANY (COALESCE(p.proargnames, ARRAY[]::text[])))
           AND p.prosrc LIKE '%job_workspace%'
           AND p.prosrc NOT LIKE '%job_tenant%'
    ) THEN
        RAISE EXCEPTION
            '017 verify: claim_ingest_job(TEXT,INT) bukan versi job_workspace '
            'yang diharapkan (owner/search_path/OUT param)';
    END IF;

    -- (b) hanya satu versi (tak ada overload nyasar)
    SELECT count(*) INTO v_n
      FROM pg_proc p
     WHERE p.pronamespace = 'public'::regnamespace
       AND p.proname = 'claim_ingest_job';
    IF v_n <> 1 THEN
        RAISE EXCEPTION '017 verify: % versi claim_ingest_job (harus 1)', v_n;
    END IF;

    -- (c) app_api boleh memanggil
    IF NOT has_function_privilege('app_api',
                                 'public.claim_ingest_job(text,integer)',
                                 'EXECUTE') THEN
        RAISE EXCEPTION '017 verify: app_api kehilangan EXECUTE claim_ingest_job';
    END IF;

    -- (d) PUBLIC TIDAK boleh memanggil. `proacl IS NULL` = ACL default
    --     (owner + PUBLIC) -> juga dianggap gagal.
    IF EXISTS (
        SELECT 1
          FROM pg_proc p
          CROSS JOIN LATERAL aclexplode(
              COALESCE(p.proacl, acldefault('f', p.proowner))) a
         WHERE p.pronamespace = 'public'::regnamespace
           AND p.proname = 'claim_ingest_job'
           AND p.pronargs = 2
           AND a.grantee = 0
           AND a.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION '017 verify: PUBLIC masih bisa EXECUTE claim_ingest_job';
    END IF;

    -- (e) invarian 008: owner fungsi butuh DML tabel (ownership tidak
    --     memberi hak tabel).
    IF NOT has_table_privilege('rag_admin', 'public.ingestion_jobs', 'UPDATE') THEN
        RAISE EXCEPTION
            '017 verify: rag_admin kehilangan UPDATE ingestion_jobs (grant 008)';
    END IF;

    RAISE NOTICE '017: claim_ingest_job -> OUT job_workspace (PUBLIC dicabut)';
END
$do$;

-- ============================================================================
-- CO-DEPLOY (WAJIB, commit yang sama — 017 TANPA ini = worker mati saat
-- klaim pertama):
--   app/infra/ingest_repo.py:177
--       row["job_tenant"]  ->  row["job_workspace"]
--   (atau lebih tahan lama: `row["job_workspace"]` diganti akses posisional
--    tidak disarankan — eksplisit lebih baik karena kolom ini kontrak.)
--   grep lain: `rg -n 'job_tenant' app/ tests/` harus bersih kecuali di
--   scripts/rename_audit.py (allowlist "out_param_fn") yang boleh dihapus
--   di rilis ini dan tests/test_frozen_contract.py (lihat di bawah).
--
-- Kalau kode lama memanggil fungsi baru, kegagalannya:
--   KeyError: 'job_tenant'   (dict hasil SELECT * ...)
-- → worker crash pada loop klaim pertama, bukan diam-diam. Itu kenapa 017
--   harus satu rilis dengan perubahan kode, bukan lebih dulu.
--
-- CATATAN 019 (di luar lingkup 016-018, temuan saat menyusun ini):
-- `get_source_secret(UUID)` juga SECURITY DEFINER + BYPASSRLS, dan 007/014
-- tidak pernah REVOKE EXECUTE dari PUBLIC — jadi role DB mana pun bisa
-- membaca secret_enc SEMUA workspace lewat fungsi itu. Perbaikannya satu
-- statement (tanpa DROP, jadi tidak perlu ikut 017):
--   REVOKE ALL ON FUNCTION get_source_secret(UUID) FROM PUBLIC;
--   GRANT EXECUTE ON FUNCTION get_source_secret(UUID) TO app_api;
-- Tidak dimasukkan ke 017 supaya migrasi ini tetap satu perubahan.
-- ============================================================================
