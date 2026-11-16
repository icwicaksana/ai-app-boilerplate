-- 016_rls_guc_workspace.sql (v0.8.2, Tier C #1): GUC RLS dua-baca.
--
-- RUANG LINGKUP:
--   * 8 policy `tenant_isolation` (5 RAG + 3 KG) diganti nama jadi
--     `workspace_isolation`.
--   * Ekspresinya dibaca DUAL: `app.current_workspace` dulu, jatuh ke
--     `app.current_tenant` bila kosong.
--   * TIDAK menyentuh kolom (sudah `workspace` sejak 014), TIDAK menyentuh
--     fungsi SECURITY DEFINER (itu 017), TIDAK menyentuh data (018).
--
-- ---------------------------------------------------------------------------
-- KENAPA COALESCE, BUKAN SWITCH LANGSUNG KE app.current_workspace
-- ---------------------------------------------------------------------------
-- `current_setting(nama, missing_ok => true)` mengembalikan NULL (bukan
-- error) bila GUC tak pernah diset pada sesi itu. Maka policy
--     workspace = current_setting('app.current_workspace', true)
-- pada sesi yang hanya menyetel `app.current_tenant` mengevaluasi
--     workspace = NULL  ->  NULL (bukan TRUE)
-- dan NULL di qual RLS = baris TIDAK lolos. Hasilnya:
--     SELECT  -> 0 baris (bukan error)
--     INSERT/UPDATE -> ditolak WITH CHECK (bukan error)
--     DELETE   -> 0 baris (bukan error)
-- Yaitu "aplikasi kosong" yang SENYAP: HTTP 200 dengan list kosong, tanpa
-- satu pun exception, tanpa alert. Worker tidak mengklaim job apa pun
-- (claim_ingest_job bypass RLS, tapi semua query sesudahnya buta) sehingga
-- job jatuh ke `dead` setelah max_attempts (poison-pill massal).
--
-- Switch langsung TIDAK BISA di-deploy atomik terhadap kode, karena runner
-- migrasi (app/infra/rag_migrate.py) berjalan di startup proses mana pun
-- yang boot lebih dulu (API ATAU worker), sementara proses lama masih hidup:
--   - DB dimigrasi proses pertama, kode yang menyetel GUC baru baru ada
--     setelah SEMUA proses di-restart.
--   - Jendela di antaranya = outage senyap di atas, dan rollback aplikasi
--     (deploy kode lama) akan mengulanginya karena migrasi sudah tercatat.
-- COALESCE membuat urutan apa pun aman:
--   kode lama  : set `tenant` saja          -> cabang ke-2 yang dipakai
--   kode baru  : set KEDUANYA               -> cabang ke-1 yang dipakai
--   rollback   : kode lama lagi             -> tetap jalan (tak perlu 016-down)
-- dan sifat fail-closed dipertahankan: keduanya NULL -> 0 baris (deny),
-- tidak pernah jadi "allow semua".
--
-- URUTAN CABANG PENTING (keamanan, bukan estetika): yang baru DULU.
-- Alasannya kebocoran lintas-workspace, bukan kerapian:
--   - GUC yang bocor dari request sebelumnya (SET session-level, bukan
--     SET LOCAL) hanya berbahaya bila ia MENANG.
--   - Dengan cabang baru-dulu: kode baru selalu menyetel keduanya secara
--     konsisten, jadi tidak ada nilai basi yang menang.
--   - Bila cabang dibalik (tenant dulu), kode lama yang hanya menyetel
--     `tenant` akan MENGABAIKAN `app.current_workspace` yang masih basi di
--     koneksi pool -> baca lintas-workspace.
-- Karena itu seluruh penulisan GUC WAJIB `set_config(..., true)` (is_local)
-- di dalam transaksi eksplisit — pola yang sudah dipakai repo ini
-- (ingest_repo.py, pg_graph.py, pgvector.py, reindex.py). JANGAN pernah
-- memakai SET session-level untuk GUC ini.
--
-- `NULLIF(..., '')` bukan hiasan: GUC yang disetel ke string kosong
-- (`SET LOCAL app.current_workspace = ''`, atau placeholder dari
-- `ALTER DATABASE ... SET`) bernilai '' — BUKAN NULL. Tanpa NULLIF, ''
-- menang atas fallback dan hasilnya 0 baris lagi. Dengan NULLIF, '' ==
-- "tidak diset" dan fallback tetap bekerja.
--
-- ATURAN EKSEKUSI (D18): via app/infra/rag_migrate.py. Satu berkas = satu
-- transaksi. Karena itu:
--   * DILARANG BEGIN/COMMIT (nested transaction error).
--   * DILARANG CREATE INDEX CONCURRENTLY.
--   * Berkas ini TIDAK menulis schema_migrations (runner yang menulis).
--   * ALTER POLICY = katalog saja (cepat), tetapi menahan ACCESS EXCLUSIVE
--     di 8 tabel sampai COMMIT; lock_timeout membuatnya gagal cepat, bukan
--     mengantre di belakang query panjang.
--
-- ROLLBACK: migrations/016_rls_guc_workspace.rollback.sql (manual, tidak
-- pernah didaftarkan di MIGRATIONS). Aman dijalankan kapan pun selama kode
-- baru masih menyetel `app.current_tenant` juga (dual-set) — itulah gunanya
-- fase ini.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- 1) PREFLIGHT — validasi SEMUA tabel sebelum satu pun DDL diambil.
--    Kegagalan harus terjadi sebelum ACCESS EXCLUSIVE pertama.
--    Yang dicek per tabel:
--      a) tabelnya ada (007/011/013 belum lengkap = jangan lanjut),
--      b) kolom `workspace` ada (014 sudah jalan),
--      c) TEPAT satu policy di antara {tenant_isolation, workspace_isolation};
--         dua-duanya ada = drift manual, nol = RLS FORCED tanpa policy
--         (deny-all senyap) — keduanya harus GAGAL KERAS, bukan "dilewati".
--      d) RLS masih ENABLED + FORCED (016 tak boleh diam-diam melonggarkan).
DO $do$
DECLARE
    r        RECORD;
    v_old    INT;
    v_new    INT;
    v_col    INT;
    v_rls    BOOLEAN;
    v_force  BOOLEAN;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('documents'), ('ingestion_jobs'), ('chunks'),
            ('source_connections'), ('source_watermarks'),
            ('kg_entities'), ('kg_edges'), ('kg_aliases')
        ) AS v(tbl)
    LOOP
        IF to_regclass(format('public.%I', r.tbl)) IS NULL THEN
            RAISE EXCEPTION
                '016 preflight: tabel % tidak ada — 007/011/013 belum lengkap',
                r.tbl;
        END IF;

        SELECT count(*) INTO v_col
          FROM pg_attribute a
         WHERE a.attrelid = format('public.%I', r.tbl)::regclass
           AND a.attname = 'workspace'
           AND a.attnum > 0
           AND NOT a.attisdropped;
        IF v_col <> 1 THEN
            RAISE EXCEPTION
                '016 preflight: % tak punya kolom workspace — 014 belum jalan',
                r.tbl;
        END IF;

        SELECT count(*) FILTER (WHERE policyname = 'tenant_isolation'),
               count(*) FILTER (WHERE policyname = 'workspace_isolation')
          INTO v_old, v_new
          FROM pg_policies
         WHERE schemaname = 'public' AND tablename = r.tbl;

        IF v_old = 1 AND v_new = 1 THEN
            RAISE EXCEPTION
                '016 preflight: % punya KEDUA policy (tenant_isolation + '
                'workspace_isolation) — drift, periksa manual', r.tbl;
        END IF;
        IF v_old + v_new <> 1 THEN
            RAISE EXCEPTION
                '016 preflight: % punya % policy tenant/workspace_isolation '
                '(harus 1) — RLS FORCED tanpa policy = deny-all senyap',
                r.tbl, v_old + v_new;
        END IF;

        SELECT c.relrowsecurity, c.relforcerowsecurity INTO v_rls, v_force
          FROM pg_class c
         WHERE c.oid = format('public.%I', r.tbl)::regclass;
        IF NOT v_rls OR NOT v_force THEN
            RAISE EXCEPTION
                '016 preflight: % RLS enabled=% forced=% (harus keduanya true)',
                r.tbl, v_rls, v_force;
        END IF;
    END LOOP;
END
$do$;

-- 2) RENAME POLICY `tenant_isolation` -> `workspace_isolation`.
--    ALTER POLICY ... RENAME tidak punya IF EXISTS, jadi WAJIB dijaga DO:
--    di DB yang sudah 016, policy bernama baru sudah ada -> no-op idempoten.
--    Rename policy = metadata-only: OID policy, role target, dan command
--    (`ALL`) tidak berubah; yang berubah hanya namanya. Tidak ada jendela
--    "RLS aktif tanpa policy" (rename bukan DROP).
DO $do$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('documents'), ('ingestion_jobs'), ('chunks'),
            ('source_connections'), ('source_watermarks'),
            ('kg_entities'), ('kg_edges'), ('kg_aliases')
        ) AS v(tbl)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public' AND tablename = r.tbl
               AND policyname = 'tenant_isolation'
        ) THEN
            CONTINUE;  -- sudah workspace_isolation (re-run) atau tak ada
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public' AND tablename = r.tbl
               AND policyname = 'workspace_isolation'
        ) THEN
            RAISE EXCEPTION
                '016: % punya kedua policy — drift', r.tbl;
        END IF;
        EXECUTE format(
            'ALTER POLICY tenant_isolation ON public.%I RENAME TO '
            'workspace_isolation', r.tbl);
        RAISE NOTICE '016: %.tenant_isolation -> workspace_isolation', r.tbl;
    END LOOP;
END
$do$;

-- 3) EKSPRESI DUAL-BACA — ditulis EKSPLISIT per tabel (bukan loop) karena
--    ini batas keamanan: harus terbaca di review, bukan tersembunyi.
--
--    ALTER POLICY (bukan DROP+CREATE): policy sudah dijamin ada oleh 014
--    (USING + WITH CHECK di kedelapan tabel) dan preflight di atas, jadi
--    yang perlu diganti hanya ekspresinya. ALTER POLICY mempertahankan OID
--    policy dan role target — lebih kecil risikonya daripada DROP+CREATE.
--
--    Bentuk kanonis (IDENTIK di USING dan WITH CHECK — kalau berbeda,
--    INSERT bisa menulis baris yang tak bisa dibaca sesi itu sendiri):
--        workspace = COALESCE(
--            NULLIF(current_setting('app.current_workspace', true), ''),
--            NULLIF(current_setting('app.current_tenant', true), '')
--        )
ALTER POLICY workspace_isolation ON documents
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

ALTER POLICY workspace_isolation ON ingestion_jobs
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

ALTER POLICY workspace_isolation ON chunks
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

ALTER POLICY workspace_isolation ON source_connections
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

ALTER POLICY workspace_isolation ON source_watermarks
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

ALTER POLICY workspace_isolation ON kg_entities
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

ALTER POLICY workspace_isolation ON kg_edges
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

ALTER POLICY workspace_isolation ON kg_aliases
    USING (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')))
    WITH CHECK (workspace = COALESCE(
        NULLIF(current_setting('app.current_workspace', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')));

--    approvals / audit_log / app_users TETAP tanpa RLS (keputusan 007/012/014:
--    memperkenalkan RLS di jalur auth/HITL = perubahan perilaku, bukan GUC).

-- 4) VERIFIKASI — gagal = seluruh transaksi rollback, tak ada state separuh.
DO $do$
DECLARE
    v_n     INT;
    v_old   TEXT;
    v_order TEXT;
BEGIN
    -- (a) 8 policy `workspace_isolation`, dual-baca, USING **dan** WITH CHECK.
    SELECT count(*)
      INTO v_n
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'workspace_isolation'
       AND tablename = ANY (ARRAY[
             'documents', 'ingestion_jobs', 'chunks', 'source_connections',
             'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases'])
       AND qual      ILIKE '%app.current_workspace%'
       AND qual      ILIKE '%app.current_tenant%'
       AND qual      ILIKE '%coalesce%'
       AND with_check ILIKE '%app.current_workspace%'
       AND with_check ILIKE '%app.current_tenant%'
       AND with_check ILIKE '%coalesce%';
    IF v_n <> 8 THEN
        RAISE EXCEPTION
            '016 verify: policy workspace_isolation dual-baca kurang (% dari 8)',
            v_n;
    END IF;

    -- (b) tak boleh ada sisa nama lama di 8 tabel itu.
    SELECT string_agg(tablename, ', ')
      INTO v_old
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'tenant_isolation'
       AND tablename = ANY (ARRAY[
             'documents', 'ingestion_jobs', 'chunks', 'source_connections',
             'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases']);
    IF v_old IS NOT NULL THEN
        RAISE EXCEPTION '016 verify: policy tenant_isolation masih ada di %',
                        v_old;
    END IF;

    -- (c) URUTAN cabang: workspace HARUS muncul sebelum tenant di ekspresi.
    --     Ini jangkar keamanan (lihat alasan di header) — kalau terbalik,
    --     GUC basi dari koneksi pool bisa menang.
    SELECT string_agg(tablename, ', ')
      INTO v_order
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'workspace_isolation'
       AND (strpos(lower(qual), 'app.current_workspace')
            >= strpos(lower(qual), 'app.current_tenant')
         OR strpos(lower(qual), 'app.current_workspace') = 0);
    IF v_order IS NOT NULL THEN
        RAISE EXCEPTION
            '016 verify: urutan cabang salah (tenant mendahului workspace) di %',
            v_order;
    END IF;

    -- (d) RLS tidak boleh ikut berubah statusnya.
    SELECT count(*) INTO v_n
      FROM pg_class c
     WHERE c.oid = ANY (ARRAY[
             to_regclass('public.documents'),
             to_regclass('public.ingestion_jobs'),
             to_regclass('public.chunks'),
             to_regclass('public.source_connections'),
             to_regclass('public.source_watermarks'),
             to_regclass('public.kg_entities'),
             to_regclass('public.kg_edges'),
             to_regclass('public.kg_aliases')])
       AND c.relrowsecurity AND c.relforcerowsecurity;
    IF v_n <> 8 THEN
        RAISE EXCEPTION '016 verify: RLS enabled+forced hanya di % dari 8', v_n;
    END IF;

    RAISE NOTICE '016: GUC dual-baca + policy workspace_isolation selesai';
END
$do$;

-- ============================================================================
-- CO-DEPLOY (WAJIB, commit yang sama):
-- 1. app/infra/rag_migrate.py: tambahkan ("016", "016_rls_guc_workspace.sql").
-- 2. Setiap penulisan GUC harus menyetel KEDUANYA dalam satu round-trip,
--    tetap `true` (local!) di dalam `conn.transaction()`:
--      SELECT set_config('app.current_workspace', %s, true),
--             set_config('app.current_tenant',    %s, true)
--    14 call site di app/: ingest_repo.py (1), rag/pg_graph.py (7),
--    rag/pgvector.py (5), workers/reindex.py (1) + 2 di tests/integration.
--    Disarankan SATU helper (mis. `_set_ws_guc(conn, ws)`) supaya tidak ada
--    lagi tempat yang bisa lupa salah satu.
-- 3. tests/test_frozen_contract.py::test_guc_rls_masih_app_current_tenant
--    meng-assert "app.current_workspace" TIDAK ada di 012 — test itu
--    mengunci keadaan pra-016. Perbarui (assert ke 016, bukan 012) di commit
--    yang sama, kalau tidak CI merah.
-- 4. scripts/rename_audit.py: entri FROZEN_ALLOWLIST "guc" tetap diperlukan
--    selama fase dual-set (kode masih menyetel app.current_tenant).
--
-- FASE 2 (JANGAN sekarang): setelah SEMUA proses menulis
-- `app.current_workspace` (dan log bersih selama 1 rilis), buang cabang
-- tenant lewat migrasi terpisah — lihat lampiran di jawaban desain. Jangan
-- gabungkan ke 016: itu menghapus satu-satunya jaring rollback.
-- ============================================================================
