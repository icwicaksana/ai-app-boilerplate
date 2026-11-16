-- 014_rename_tenant_to_workspace.sql (v0.9 D20): rename kolom `tenant` -> `workspace`.
--
-- RUANG LINGKUP (sempit, sengaja):
--   * Nama KOLOM database saja: documents, ingestion_jobs, chunks,
--     source_connections, source_watermarks, kg_entities, kg_edges,
--     kg_aliases, approvals, audit_log, app_users.
--   * Constraint UNIQUE yang menyebut tenant + index ber-prefix tenant +
--     nama policy RLS + fungsi SECURITY DEFINER (body-nya hardcode kolom).
--   * TIDAK termasuk: GUC `app.current_tenant` (tetap; ganti terpisah di 015),
--     klaim JWT `tenant`, format thread_id `tenant:user:session` (BEKU v0.3),
--     payload Qdrant `tenant`, header X-Tenant-ID. Semua itu konsep domain,
--     bukan nama kolom — rename mekanis akan memutus qdrant/reindex dan JWT.
--
-- ATURAN EKSEKUSI (D18): via `app/infra/rag_migrate.py`. Runner membungkus
-- SATU BERKAS dalam SATU transaksi (advisory lock + schema_migrations), jadi:
--   * DILARANG `CREATE INDEX CONCURRENTLY` (tak bisa dalam blok transaksi).
--     Tidak dibutuhkan: rename kolom/index/constraint = metadata-only, bukan
--     rebuild (index & constraint menyimpan attnum, bukan nama kolom).
--   * DROP POLICY + CREATE POLICY di sini ATOMIK: tidak ada jendela waktu
--     di mana RLS aktif tanpa policy. RLS (ENABLE/FORCE) tidak pernah dimatikan.
--   * Berkas TIDAK menulis schema_migrations (runner yang menulis, di transaksi
--     yang sama) — kalau ditulis di sini, INSERT kedua = PK violation.
--
-- KENAPA BANYAK GUARD (padahal runner atomik):
--   `alter table ... rename column` TIDAK punya IF EXISTS di PostgreSQL, dan
--   `create policy` gagal bila kolom belum ada. Tiga tabel app-db (app_users,
--   approvals, audit_log) dibuat oleh kode Python (`ensure_schema` +
--   `CREATE TABLE IF NOT EXISTS`), BUKAN oleh migrasi 001-006 yang tidak ada
--   di repo ini. Urutan lifespan (app/main.py) = ensure_schema -> migrasi,
--   tetapi WORKER (app/workers/ingest.py:155) memanggil migrasi TANPA
--   ensure_schema. Akibatnya sah bila ketiga tabel itu:
--     (a) belum ada (worker start pertama, DB baru), atau
--     (b) sudah dibuat kode BARU dengan kolom `workspace` (DB baru, API start).
--   Karena itu: guard per-tabel + preflight yang GAGAL KERAS pada keadaan
--   ambigu (kolom `tenant` DAN `workspace` ada = drift), bukan "sepertinya
--   sudah ada, lewati" (semangat D18).
--
-- URUTAN AMAN (ringkas; detail di komentar tiap bagian):
--   0. lock_timeout/statement_timeout (gagal cepat, jangan tumpuk antrean lock)
--   1. PREFLIGHT  : validasi semua tabel/kolom SEBELUM DDL apa pun
--   2. RENAME KOLOM (11 tabel)
--   3. RENAME CONSTRAINT (yang namanya mengandung tenant)
--   4. RENAME INDEX (yang namanya mengandung tenant)
--   5. POLICY RLS  : DROP + CREATE ulang (USING **dan** WITH CHECK)
--   6. FUNGSI      : claim_ingest_job (body hardcode kolom) + re-assert grant
--   7. VERIFIKASI  : RAISE EXCEPTION bila ada sisa `tenant` / policy salah
--
-- ROLLBACK: `migrations/014_rename_tenant_to_workspace.rollback.sql`
--   (sengaja TIDAK terdaftar di MIGRATIONS — tidak pernah dieksekusi otomatis).
--   Karena UP = 1 transaksi, rollback hanya perlu untuk "batal semantik",
--   bukan pemulihan crash: crash = seluruh berkas batal, versi tak tercatat.
--
-- CO-DEPLOY WAJIB (lihat komentar penutup): SQL di app/ + tests/ yang
-- menyebut kolom `tenant` harus ikut berubah di COMMIT yang sama.

-- 0) Budget lock. SET LOCAL = aman untuk pool (auto-reset saat COMMIT;
--    SET session-level akan BOCOR ke koneksi pool dan menulari request lain).
--    Catatan: di luar blok transaksi `SET LOCAL` hanya WARNING (diabaikan).
--    Runner selalu membungkus; bila dijalankan manual via psql, bungkus
--    dengan BEGIN/COMMIT lebih dulu.
--    Efek: bila ada query panjang/transaksi terbuka, migrasi gagal cepat
--    dengan `canceling statement due to lock timeout` alih-alih menahan
--    antrean ACCESS EXCLUSIVE di belakangnya (lock queue = outage).
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '180s';

-- 1) PREFLIGHT + 2) RENAME KOLOM.
--    Validasi dulu SEMUA tabel (tanpa DDL), baru bertindak: kegagalan prematur
--    harus terjadi sebelum satu pun ACCESS EXCLUSIVE diambil.
DO $do$
DECLARE
    r RECORD;
    v_tenant INT;
    v_ws     INT;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('documents',          TRUE),
            ('ingestion_jobs',     TRUE),
            ('chunks',             TRUE),
            ('source_connections', TRUE),
            ('source_watermarks',  TRUE),
            ('kg_entities',        TRUE),
            ('kg_edges',           TRUE),
            ('kg_aliases',         TRUE),
            ('approvals',          FALSE),
            ('audit_log',          FALSE),
            ('app_users',          FALSE)
        ) AS v(tbl, required)
    LOOP
        IF to_regclass(format('public.%I', r.tbl)) IS NULL THEN
            IF r.required THEN
                RAISE EXCEPTION
                    '014 preflight: tabel % tidak ada — 007/011/013 belum lengkap',
                    r.tbl;
            END IF;
            RAISE NOTICE '014 preflight: % belum ada (dibuat kode app) — dilewati',
                         r.tbl;
            CONTINUE;
        END IF;

        SELECT count(*) FILTER (WHERE a.attname = 'tenant'),
               count(*) FILTER (WHERE a.attname = 'workspace')
          INTO v_tenant, v_ws
          FROM pg_attribute a
         WHERE a.attrelid = format('public.%I', r.tbl)::regclass
           AND a.attnum > 0
           AND NOT a.attisdropped;

        IF v_tenant = 1 AND v_ws = 1 THEN
            RAISE EXCEPTION
                '014 preflight: %.tenant DAN %.workspace dua-duanya ada — drift, '
                'periksa manual sebelum migrasi', r.tbl, r.tbl;
        ELSIF v_tenant = 0 AND v_ws = 0 THEN
            IF r.required THEN
                RAISE EXCEPTION
                    '014 preflight: % tak punya kolom tenant maupun workspace',
                    r.tbl;
            END IF;
            RAISE NOTICE '014 preflight: % tak punya kolom tenant/workspace — dilewati',
                         r.tbl;
        END IF;
    END LOOP;

    -- Aksi: hanya tabel yang masih punya `tenant`.
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
        SELECT count(*) INTO v_tenant
          FROM pg_attribute a
         WHERE a.attrelid = format('public.%I', r.tbl)::regclass
           AND a.attname = 'tenant'
           AND a.attnum > 0
           AND NOT a.attisdropped;
        IF v_tenant = 0 THEN
            CONTINUE;  -- sudah workspace (re-run / DB baru): no-op idempoten
        END IF;
        EXECUTE format('ALTER TABLE %I RENAME COLUMN tenant TO workspace', r.tbl);
        RAISE NOTICE '014: %.tenant -> %.workspace', r.tbl, r.tbl;
    END LOOP;
END
$do$;

-- 3) RENAME CONSTRAINT yang namanya menyebut tenant (kosmetik, TAPI nama
--    auto-generated lama menyesatkan: `documents_tenant_sha256_key` kini
--    mengunci (workspace, sha256)).
--    ALTER TABLE ... RENAME CONSTRAINT = metadata-only, dan bila constraint
--    punya index, index-nya ikut berganti nama (dok. PostgreSQL ALTER TABLE:
--    "When renaming a constraint that has an underlying index, the index is
--    renamed as well"). JANGAN memilih jalan DROP CONSTRAINT + ADD CONSTRAINT:
--    itu membangun ulang index UNIQUE di bawah ACCESS EXCLUSIVE = downtime
--    pada tabel besar.
--    Catatan: PK `source_watermarks_pkey` (tenant, connection_id, table_name)
--    dan `kg_aliases_pkey` (tenant, alias_norm) TIDAK perlu rename — namanya
--    tidak menyebut tenant dan definisinya ikut attnum.
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('documents',          'documents_tenant_sha256_key',
                                   'documents_workspace_sha256_key'),
            ('source_connections', 'source_connections_tenant_name_key',
                                   'source_connections_workspace_name_key'),
            ('app_users',          'app_users_tenant_username_key',
                                   'app_users_workspace_username_key')
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
            CONTINUE;  -- sudah rename / tak pernah ada (nama beda) -> no-op
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_constraint c
             WHERE c.conrelid = format('public.%I', r.tbl)::regclass
               AND c.conname = r.new_name
        ) THEN
            RAISE EXCEPTION '014: constraint % sudah ada di % — drift',
                            r.new_name, r.tbl;
        END IF;
        EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                       r.tbl, r.old_name, r.new_name);
        RAISE NOTICE '014: constraint % -> %', r.old_name, r.new_name;
    END LOOP;
END
$do$;

-- 4) RENAME INDEX ber-prefix tenant (metadata-only; OID & statistik index
--    dipertahankan, tak ada REINDEX). ALTER INDEX RENAME hanya butuh
--    SHARE UPDATE EXCLUSIVE (dok. ALTER INDEX) — murah, tidak memblokir DML.
--    PENTING: daftar ini HANYA index biasa (bukan index milik constraint);
--    bila index milik constraint dimasukkan, ALTER INDEX ... RENAME akan ikut
--    me-rename constraint-nya dan nama di langkah 3 jadi tak sinkron.
--    Index tanpa "tenant" di namanya (idx_ent_doc, idx_ent_norm, idx_edge_seed,
--    idx_edge_bwd, approvals_pending_idx, idx_chunks_hnsw, idx_chunks_tsv*)
--    sengaja TIDAK disentuh: churn kosmetik tanpa manfaat, dan 011/docs
--    merujuk nama tsb.
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('idx_documents_tenant_status',  'idx_documents_workspace_status'),
            ('idx_documents_tenant_updated', 'idx_documents_workspace_updated'),
            ('idx_jobs_tenant_state',        'idx_jobs_workspace_state'),
            ('idx_chunks_tenant_doc',        'idx_chunks_workspace_doc'),
            ('idx_src_conn_tenant',          'idx_src_conn_workspace'),
            ('audit_tenant_idx',             'audit_workspace_idx')
        ) AS v(old_name, new_name)
    LOOP
        IF to_regclass(format('public.%I', r.old_name)) IS NULL THEN
            CONTINUE;
        END IF;
        IF to_regclass(format('public.%I', r.new_name)) IS NOT NULL THEN
            RAISE EXCEPTION '014: index % sudah ada — drift', r.new_name;
        END IF;
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.old_name, r.new_name);
        RAISE NOTICE '014: index % -> %', r.old_name, r.new_name;
    END LOOP;
END
$do$;

-- 5) POLICY RLS: DROP lalu CREATE ulang — eksplisit, satu per satu (batas
--    keamanan: jangan disembunyikan di loop).
--
--    Kenapa DROP+CREATE, bukan ALTER POLICY saja:
--      a) Ekspresi policy disimpan sebagai node tree (pg_policy.polqual);
--         rename kolom umumnya sudah terbawa otomatis. Jadi DROP+CREATE
--         BUKAN syarat mutlak untuk rename kolom — tetapi ia yang menjamin
--         teks policy di katalog persis seperti yang kita tulis, dan
--         menormalkan policy warisan yang mungkin hanya USING (bug 007,
--         ditambal 012 via ALTER POLICY): SETELAH 014, kedelapan tabel
--         dijamin USING + WITH CHECK, apa pun riwayat DB-nya.
--      b) ALTER POLICY hanya bisa mengubah ekspresi/role policy yang SUDAH
--         ada; ia tidak bisa membuat policy yang hilang, dan tidak bisa
--         mengganti nama kolom referensi bila policy lama rusak. DROP+CREATE
--         idempoten di kedua arah (DROP POLICY IF EXISTS).
--
--    GUC TETAP `app.current_tenant` (bukan app.current_workspace): 20+ call
--    site `SELECT set_config('app.current_tenant', ...)` di ingest_repo.py,
--    pg_graph.py, pgvector.py, reindex.py, dan tests. Mengganti nama GUC di
--    migrasi yang sama = menggandakan blast radius pada batas keamanan.
--    Rename GUC dilakukan terpisah (015) dengan pola dua-fase:
--    015a: policy membaca coalesce(workspace-GUC, tenant-GUC) + app menulis
--    keduanya; 015b: hapus cabang tenant setelah semua deployment baru.
--
--    `current_setting(..., true)` = missing_ok: GUC tak diset -> NULL ->
--    `workspace = NULL` = NULL -> tidak ada baris (fail-closed). Perilaku
--    identik dengan 007/012/013; jangan "memperbaiki" jadi NOT NULL check
--    di migrasi ini.
DROP POLICY IF EXISTS tenant_isolation ON documents;
CREATE POLICY tenant_isolation ON documents
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON ingestion_jobs;
CREATE POLICY tenant_isolation ON ingestion_jobs
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON chunks;
CREATE POLICY tenant_isolation ON chunks
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON source_connections;
CREATE POLICY tenant_isolation ON source_connections
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON source_watermarks;
CREATE POLICY tenant_isolation ON source_watermarks
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON kg_entities;
CREATE POLICY tenant_isolation ON kg_entities
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON kg_edges;
CREATE POLICY tenant_isolation ON kg_edges
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS tenant_isolation ON kg_aliases;
CREATE POLICY tenant_isolation ON kg_aliases
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

--    approvals / audit_log / app_users TIDAK diberi RLS di sini: 007/012
--    menyatakan keduanya di luar scope v0.8 (memperkenalkan RLS di sana =
--    perubahan perilaku auth/HITL, bukan rename kolom). app_users pun tak
--    pernah punya RLS. Konsistensi perilaku > konsistensi nama.

-- 6) FUNGSI SECURITY DEFINER.
--
--    claim_ingest_job: body PL/pgSQL disimpan sebagai TEKS (bukan node tree),
--    jadi `j.tenant` di body TIDAK ikut berubah oleh RENAME COLUMN — fungsi
--    tetap valid sampai dipanggil, lalu gagal `column j.tenant does not exist`.
--    Karena itu body WAJIB di-CREATE OR REPLACE di transaksi yang sama.
--
--    Signature & tipe kembalian DIPERTAHANKAN persis (TEXT, INT DEFAULT 120,
--    RETURNS TABLE(job_id, job_tenant, document_id)) supaya:
--      * CREATE OR REPLACE sah (mengganti signature/tipe kembalian — termasuk
--        nama kolom OUT seperti `job_tenant` — DITOLAK; harus DROP+CREATE,
--        dan DROP membuang OWNER + GRANT sehingga harus di-ALTER/GRANT ulang);
--      * app/infra/ingest_repo.py:177 (`row["job_tenant"]`) tidak berubah.
--        Rename OUT param -> `job_workspace` = follow-up terpisah, bukan di
--        migrasi ini (satu perubahan besar per migrasi).
--    Nama kolom OUT `job_tenant` sengaja tetap: ia kontrak API fungsi, bukan
--    nama kolom tabel.
--
--    DROP 1-arg = peninggalan 007 (sudah dibuang 009). DROP FUNCTION mencocokkan
--    TIPE ARGUMEN PERSIS, jadi ini TIDAK menyentuh versi (TEXT, INT) yang
--    punya DEFAULT — aman & no-op di DB yang sudah 009.
DROP FUNCTION IF EXISTS claim_ingest_job(TEXT);

CREATE OR REPLACE FUNCTION claim_ingest_job(p_worker TEXT,
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
    job_tenant := v_workspace;   -- nama kolom OUT dipertahankan (kontrak API)
    document_id := v_doc;
    RETURN NEXT;
END;
$func$;

ALTER FUNCTION claim_ingest_job(TEXT, INT) OWNER TO rag_admin;
GRANT EXECUTE ON FUNCTION claim_ingest_job(TEXT, INT) TO app_api;

--    get_source_secret: body-nya (LANGUAGE sql, teks) hanya menyentuh
--    `secret_enc` dan `id` — TIDAK menyebut tenant, jadi rename kolom tidak
--    merusaknya dan tidak perlu CREATE OR REPLACE. Yang dilakukan di sini
--    hanya: (a) re-assert OWNER/GRANT (idempoten, jaring pengaman bila ada
--    DB yang pernah DROP+CREATE fungsi ini), dan (b) pin search_path —
--    SECURITY DEFINER tanpa search_path tetap adalah kelas CVE (objek di
--    schema lain bisa membajak resolusi nama). body tidak diubah.
ALTER FUNCTION get_source_secret(UUID) OWNER TO rag_admin;
ALTER FUNCTION get_source_secret(UUID) SET search_path = public;
GRANT EXECUTE ON FUNCTION get_source_secret(UUID) TO app_api;

--    GRANT tabel TIDAK perlu diulang: GRANT/REVOKE terikat OID tabel, dan
--    rename kolom tidak mengubah OID. Grant `rag_admin` dari 008 juga tetap
--    (owner fungsi saja tidak memberi hak tabel — itu sebabnya 008 ada).
--
--    KOREKSI atas asumsi awal: `REVOKE ALL (secret_enc)` dari 007 ternyata
--    TIDAK PERNAH BERLAKU (ACL Postgres tidak punya entri negatif; grant
--    tabel dari 007 baris 175 menang). Rename kolom tidak memperburuknya,
--    tapi juga tidak memperbaikinya. Perbaikan ada di migrasi 015.

-- 7) VERIFIKASI (gagal = seluruh transaksi rollback; tak ada state separuh).
DO $do$
DECLARE
    v_bad  TEXT;
    v_miss TEXT;
    v_n    INT;
BEGIN
    -- (a) tak boleh ada kolom `tenant` tersisa di 11 tabel.
    SELECT string_agg(format('%s.%s', c.relname, a.attname), ', ')
      INTO v_bad
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
     WHERE c.relnamespace = 'public'::regnamespace
       AND c.relkind IN ('r', 'p')
       AND c.relname = ANY (ARRAY[
             'documents', 'ingestion_jobs', 'chunks', 'source_connections',
             'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases',
             'approvals', 'audit_log', 'app_users'])
       AND a.attname = 'tenant'
       AND a.attnum > 0
       AND NOT a.attisdropped;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '014 verify: kolom tenant masih ada: %', v_bad;
    END IF;

    -- (b) setiap tabel yang ada WAJIB punya kolom workspace.
    SELECT string_agg(x.tbl, ', ')
      INTO v_miss
      FROM unnest(ARRAY[
             'documents', 'ingestion_jobs', 'chunks', 'source_connections',
             'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases',
             'approvals', 'audit_log', 'app_users']) AS x(tbl)
     WHERE to_regclass(format('public.%I', x.tbl)) IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = format('public.%I', x.tbl)::regclass
              AND a.attname = 'workspace'
              AND a.attnum > 0
              AND NOT a.attisdropped);
    IF v_miss IS NOT NULL THEN
        RAISE EXCEPTION '014 verify: tabel tanpa kolom workspace: %', v_miss;
    END IF;

    -- (c) 8 policy tenant_isolation ada, memakai workspace, USING **dan**
    --     WITH CHECK, dan tetap membaca GUC app.current_tenant.
    SELECT count(*)
      INTO v_n
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'tenant_isolation'
       AND tablename = ANY (ARRAY[
             'documents', 'ingestion_jobs', 'chunks', 'source_connections',
             'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases'])
       AND qual LIKE '%workspace%'
       AND qual LIKE '%app.current_tenant%'
       AND with_check LIKE '%workspace%';
    IF v_n <> 8 THEN
        RAISE EXCEPTION
            '014 verify: policy tenant_isolation salah/kurang (% dari 8)', v_n;
    END IF;

    -- (d) claim_ingest_job(TEXT,INT): masih SECURITY DEFINER, search_path
    --     terpin, dan body tak lagi merujuk tenant. `job_tenant` (nama OUT)
    --     sengaja tidak ikut dicocokkan.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = 'claim_ingest_job'
           AND p.pronargs = 2
           AND p.prosecdef
           AND 'search_path=public' = ANY (COALESCE(p.proconfig, ARRAY[]::text[]))
           AND p.prosrc NOT LIKE '%j.tenant%'
           AND p.prosrc NOT LIKE '%v_tenant%'
    ) THEN
        RAISE EXCEPTION
            '014 verify: claim_ingest_job(TEXT,INT) belum pakai workspace';
    END IF;

    -- (e) get_source_secret tetap SECURITY DEFINER + search_path terpin.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = 'get_source_secret'
           AND p.pronargs = 1
           AND p.prosecdef
           AND 'search_path=public' = ANY (COALESCE(p.proconfig, ARRAY[]::text[]))
    ) THEN
        RAISE EXCEPTION '014 verify: get_source_secret belum ter-harden';
    END IF;

    -- (f) invarian hak akses bertahan: rag_admin (owner fungsi) butuh DML
    --     ingestion_jobs (008).
    --     CATATAN: cek "app_api tidak boleh SELECT secret_enc" DIPINDAH ke
    --     migrasi 015. Saat 014 pertama dijalankan, cek itu GAGAL — dan
    --     penyebabnya BUKAN rename: 007 memakai pola yang tidak berlaku di
    --     PostgreSQL (`GRANT SELECT ON tabel` lalu `REVOKE ALL (secret_enc)`;
    --     ACL tidak punya entri negatif sehingga grant tabel menang). Lubang
    --     itu sudah ada sejak v0.7. Memverifikasinya di sini akan memblokir
    --     rename karena bug yang tidak ia sebabkan — dan menyembunyikannya
    --     dengan melonggarkan cek juga salah. Karena itu: dipisah ke 015.
    IF NOT has_table_privilege('rag_admin', 'public.ingestion_jobs', 'UPDATE') THEN
        RAISE EXCEPTION
            '014 verify: rag_admin kehilangan UPDATE ingestion_jobs (grant 008)';
    END IF;

    RAISE NOTICE '014: rename tenant -> workspace selesai & terverifikasi';
END
$do$;

-- ============================================================================
-- CO-DEPLOY (bukan SQL; WAJIB di commit yang sama — 014 tanpa ini =
-- app error `column tenant does not exist` di hampir semua endpoint):
--
-- 1. Registrasi runner: app/infra/rag_migrate.py
--      ("014", "014_rename_tenant_to_workspace.sql")  <- tambahkan di MIGRATIONS
--    dan perbarui MIGRATION_VERSION yang masih "007" (stale).
--    JANGAN daftarkan lebih dulu tanpa rename kode: startup berikutnya akan
--    menjalankan 014 terhadap kode lama dan mematikan app.
--
-- 2. SQL identifier (kolom) -> workspace, TAPI pertahankan nama Python
--    `tenant=`, klaim JWT `tenant`, dan format thread_id:
--      app/infra/ingest_repo.py        (~20 query, + `row["job_tenant"]` tetap)
--      app/infra/rag/pg_graph.py       (~13, termasuk
--                                       `ON CONFLICT (tenant, alias_norm)`)
--      app/infra/rag/pgvector.py       (~7)
--      app/infra/users.py              (_SCHEMA + 2 query)
--      app/infra/approvals.py          (_SCHEMA_STATEMENTS + 7 query)
--      app/workers/ingest.py           (SQL `WHERE tenant`; `job["tenant"]`)
--      app/workers/reindex.py          (`WHERE c.tenant`; `DISTINCT tenant`)
-- 3. Kunci dict hasil SELECT -> `row["workspace"]` di:
--      app/core/approvals.py:195, app/workers/ingest.py:243 & 720,
--      app/workers/reindex.py:206.
--    JEBAKAN: verify_chain/append_audit harus TETAP memakai kunci dict
--    `"tenant"` di dalam body hash (lihat approvals.py:242) — itu isi
--    hash-chain yang sudah tercatat. Mengganti kunci jadi "workspace"
--    membuat SELURUH rantai audit lama tampak rusak.
-- 4. JANGAN rename: payload Qdrant `tenant` (qdrant.py point_id/filter),
--    klaim JWT/`X-Tenant-ID`, `tenant:user:session`, argumen CLI `--tenant`.
-- 5. tests: tests/integration/test_reindex_live.py, test_retriever_contract.py,
--    test_graph_lifecycle_live.py (+ assertion kolom di test unit).
-- 6. Urutan deploy: stop worker -> deploy API+worker BARU -> migrasi jalan
--    otomatis di startup (advisory lock) -> smoke test -> start worker.
--    Jendela tulis: rename menahan ACCESS EXCLUSIVE di 11 tabel sampai COMMIT
--    (satu transaksi) — lock_timeout 5s membuatnya gagal cepat, bukan macet.
-- ============================================================================
