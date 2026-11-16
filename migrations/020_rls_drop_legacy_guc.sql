-- 020_rls_drop_legacy_guc.sql (v0.8.3): policy RLS hanya membaca GUC baru.
--
-- LATAR: 016 memakai COALESCE(workspace, tenant) supaya urutan deploy apa pun
-- aman selama transisi (kode lama hanya menulis GUC lama, kode baru hanya
-- menulis GUC baru). Sejak v0.8.2 SEMUA kode menulis `app.current_workspace`,
-- dan tidak ada deployment berjalan yang masih memakai binary lama.
-- Cabang fallback itu kini hanya menambah satu jalur yang bisa disalahgunakan
-- (GUC lama basi yang bocor di koneksi pool bisa menang bila urutannya salah).
--
-- PERBAIKAN: policy membaca HANYA `app.current_workspace`.
--
-- FAIL-CLOSED tetap terjaga: `current_setting(nama, true)` mengembalikan NULL
-- bila belum diset, sehingga `workspace = NULL` = NULL (bukan TRUE) → 0 baris.
-- GUC yang diset `''` juga menghasilkan 0 baris (tidak ada workspace bernama
-- string kosong). Jadi hilangnya GUC tetap MENOLAK, tidak pernah "allow semua".
--
-- ALTER POLICY dipakai (bukan DROP+CREATE) supaya tidak ada jendela waktu
-- RLS aktif tanpa policy. Preflight menolak keadaan drift.
--
-- Tidak ada BEGIN/COMMIT: runner membungkus satu berkas dalam satu transaksi.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $do$
DECLARE
    r       RECORD;
    v_n     INT;
    v_legacy INT;
BEGIN
    -- 1) Preflight: kedelapan policy harus ada, dan TIDAK boleh sudah bersih
    --    semua (kalau bersih, ini DB yang sudah dimigrasikan → skip, bukan
    --    error; tetapi kalau sebagian, itu drift → gagal keras).
    SELECT count(*) INTO v_n
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'workspace_isolation'
       AND tablename = ANY (ARRAY[
             'documents', 'ingestion_jobs', 'chunks', 'source_connections',
             'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases']);
    IF v_n = 0 THEN
        RAISE NOTICE '020: tidak ada policy workspace_isolation — dilewati';
        RETURN;
    END IF;
    IF v_n <> 8 THEN
        RAISE EXCEPTION
            '020 preflight: policy workspace_isolation hanya % dari 8 — drift',
            v_n;
    END IF;

    -- 2) Tulis ulang ekspresi: hanya GUC kanonis.
    FOR r IN
        SELECT tablename FROM pg_policies
         WHERE schemaname = 'public'
           AND policyname = 'workspace_isolation'
           AND tablename = ANY (ARRAY[
                 'documents', 'ingestion_jobs', 'chunks', 'source_connections',
                 'source_watermarks', 'kg_entities', 'kg_edges', 'kg_aliases'])
    LOOP
        EXECUTE format(
            'ALTER POLICY workspace_isolation ON %I '
            'USING (workspace = current_setting(''app.current_workspace'', true)) '
            'WITH CHECK (workspace = current_setting(''app.current_workspace'', true))',
            r.tablename);
    END LOOP;

    -- 3) Verifikasi: tidak boleh ada sisa referensi ke GUC lama, dan
    --    kedelapan policy harus masih punya USING + WITH CHECK.
    SELECT count(*) INTO v_legacy
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'workspace_isolation'
       AND (coalesce(qual, '') LIKE '%current_tenant%'
            OR coalesce(with_check, '') LIKE '%current_tenant%');
    IF v_legacy > 0 THEN
        RAISE EXCEPTION
            '020 verify: % policy masih merujuk GUC lama', v_legacy;
    END IF;

    SELECT count(*) INTO v_n
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname = 'workspace_isolation'
       AND qual IS NOT NULL
       AND with_check IS NOT NULL;
    IF v_n <> 8 THEN
        RAISE EXCEPTION
            '020 verify: hanya % policy punya USING+WITH CHECK (butuh 8)', v_n;
    END IF;

    RAISE NOTICE '020: policy RLS kini hanya membaca app.current_workspace';
END
$do$;
