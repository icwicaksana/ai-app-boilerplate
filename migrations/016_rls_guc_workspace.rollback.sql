-- 016_rls_guc_workspace.rollback.sql (down untuk 016 — MANUAL, tidak pernah
-- didaftarkan di MIGRATIONS).
--
-- KAPAN dipakai: hanya untuk "batal semantik" (mis. ternyata ada deployment
-- lama yang menyetel GUC lain). Tidak perlu untuk pemulihan crash: 016 = 1
-- transaksi, crash = seluruh berkas batal dan versi tak tercatat.
--
-- AMAN terhadap kode BARU: kode v0.8.2 tetap menyetel `app.current_tenant`
-- (dual-set, fase 1). Kalau tidak — rollback ini akan mengosongkan aplikasi
-- secara senyap, persis jebakan yang dijelaskan di header 016.
--
-- MENGHAPUS fallback ke `app.current_workspace`: setelah rollback ini, sesi
-- yang HANYA menyetel GUC baru akan melihat 0 baris. Pastikan tak ada.
--
-- Jalankan dalam transaksi (runner membungkus; psql manual: BEGIN ... COMMIT).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- 1) PREFLIGHT: 8 tabel ada, TEPAT satu policy workspace/tenant_isolation.
DO $do$
DECLARE
    r   RECORD;
    v_n INT;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('documents'), ('ingestion_jobs'), ('chunks'),
            ('source_connections'), ('source_watermarks'),
            ('kg_entities'), ('kg_edges'), ('kg_aliases')
        ) AS v(tbl)
    LOOP
        IF to_regclass(format('public.%I', r.tbl)) IS NULL THEN
            RAISE EXCEPTION '016-down preflight: tabel % tidak ada', r.tbl;
        END IF;
        SELECT count(*) INTO v_n
          FROM pg_policies
         WHERE schemaname = 'public' AND tablename = r.tbl
           AND policyname IN ('tenant_isolation', 'workspace_isolation');
        IF v_n <> 1 THEN
            RAISE EXCEPTION
                '016-down preflight: % punya % policy tenant/workspace_isolation '
                '(harus 1)', r.tbl, v_n;
        END IF;
    END LOOP;
END
$do$;

-- 2) Kembalikan ekspresi 014 (baca `app.current_tenant` saja) SEBELUM rename,
--    supaya rename di langkah 3 pasti nama lama. Dua-duanya metadata-only.
ALTER POLICY workspace_isolation ON documents
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

ALTER POLICY workspace_isolation ON ingestion_jobs
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

ALTER POLICY workspace_isolation ON chunks
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

ALTER POLICY workspace_isolation ON source_connections
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

ALTER POLICY workspace_isolation ON source_watermarks
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

ALTER POLICY workspace_isolation ON kg_entities
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

ALTER POLICY workspace_isolation ON kg_edges
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

ALTER POLICY workspace_isolation ON kg_aliases
    USING (workspace = current_setting('app.current_tenant', true))
    WITH CHECK (workspace = current_setting('app.current_tenant', true));

-- 3) Rename balik ke nama 007/011/012/013/014 (idempoten, dijaga DO).
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
        IF EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public' AND tablename = r.tbl
               AND policyname = 'tenant_isolation'
        ) THEN
            CONTINUE;  -- sudah nama lama
        END IF;
        EXECUTE format(
            'ALTER POLICY workspace_isolation ON public.%I RENAME TO '
            'tenant_isolation', r.tbl);
        RAISE NOTICE '016-down: %.workspace_isolation -> tenant_isolation',
                     r.tbl;
    END LOOP;
END
$do$;

-- 4) VERIFIKASI
DO $do$
DECLARE
    v_n INT;
BEGIN
    SELECT count(*) INTO v_n
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'tenant_isolation'
       AND tablename = ANY (ARRAY[
             'documents', 'ingestion_jobs', 'chunks', 'source_connections',
             'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases'])
       AND qual ILIKE '%app.current_tenant%'
       AND qual NOT ILIKE '%app.current_workspace%'
       AND with_check ILIKE '%app.current_tenant%';
    IF v_n <> 8 THEN
        RAISE EXCEPTION
            '016-down verify: policy tenant_isolation lama kurang (% dari 8)',
            v_n;
    END IF;
    RAISE NOTICE '016-down: policy kembali ke tenant_isolation (GUC lama saja)';
END
$do$;

-- CATATAN: baris `schema_migrations` TIDAK dihapus di sini. Untuk membuat
-- runner menjalankan 016 lagi setelah rollback, hapus manual:
--   DELETE FROM schema_migrations WHERE version = '016';
