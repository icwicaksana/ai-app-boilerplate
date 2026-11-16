-- 015_fix_secret_enc_grant.sql (v0.8.1): tutup lubang baca `secret_enc`.
--
-- TEMUAN (gate F4b, saat 014 pertama dijalankan): role `app_api` BISA
-- `SELECT secret_enc` meski 007 menulis
--     REVOKE ALL (secret_enc) ON source_connections FROM app_api;
--
-- SEBAB: PostgreSQL tidak punya ACL negatif. `GRANT SELECT, INSERT, UPDATE,
-- DELETE ON source_connections TO app_api` (007 baris 175) memberi hak pada
-- SEMUA kolom; `REVOKE ... (secret_enc)` sesudahnya tidak mencabut apa pun —
-- hak tabel tetap berlaku. Terbukti di DB: `pg_attribute.attacl` untuk
-- secret_enc = NULL (tak ada ACL kolom), sementara
-- `information_schema.column_privileges` menampilkan SELECT untuk app_api.
--
-- DAMPAK: kredensial konektor (terenkripsi Fernet) bisa dibaca langsung oleh
-- role aplikasi, melewati `get_source_secret()` yang audited. Sudah ada sejak
-- v0.7; rename kolom tidak menyebabkannya.
--
-- PERBAIKAN: cabut hak TABEL, beri ulang PER KOLOM tanpa secret_enc untuk
-- SELECT. Tulis secret_enc tetap diizinkan (write-only secret). Baca hanya
-- lewat `get_source_secret()` (SECURITY DEFINER, search_path ter-pin).
--
-- CATATAN URUTAN: migrasi ini berjalan SESUDAH 014, jadi kolomnya sudah
-- bernama `workspace` (bukan `tenant`). Jangan dijalankan mendahului 014.
--
-- Tidak ada BEGIN/COMMIT: runner (app/infra/rag_migrate.py) membungkus satu
-- berkas dalam satu transaksi. Menambah BEGIN/COMMIT di sini = nested
-- transaction error.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $do$
BEGIN
    IF to_regclass('public.source_connections') IS NULL THEN
        RAISE NOTICE '015: source_connections belum ada — dilewati';
        RETURN;
    END IF;

    -- 1) Cabut SEMUA hak tabel (inilah yang selama ini menang atas revoke
    --    kolom di 007).
    REVOKE ALL ON source_connections FROM app_api;

    -- 2) Beri ulang per kolom. SELECT sengaja TANPA secret_enc.
    GRANT SELECT (id, workspace, name, engine, host, port, dbname, username,
                  read_only, allowed_tables, created_at)
        ON source_connections TO app_api;
    GRANT INSERT (id, workspace, name, engine, host, port, dbname, username,
                  secret_enc, read_only, allowed_tables, created_at)
        ON source_connections TO app_api;
    GRANT UPDATE (id, workspace, name, engine, host, port, dbname, username,
                  secret_enc, read_only, allowed_tables, created_at)
        ON source_connections TO app_api;
    GRANT DELETE ON source_connections TO app_api;

    -- 3) Verifikasi: baca DITOLAK, tulis DIIZINKAN, kolom biasa tetap terbaca.
    IF has_column_privilege('app_api', 'public.source_connections',
                            'secret_enc', 'SELECT') THEN
        RAISE EXCEPTION '015 verify: app_api masih bisa SELECT secret_enc';
    END IF;
    IF NOT has_column_privilege('app_api', 'public.source_connections',
                                'secret_enc', 'INSERT') THEN
        RAISE EXCEPTION '015 verify: app_api kehilangan INSERT secret_enc';
    END IF;
    IF NOT has_column_privilege('app_api', 'public.source_connections',
                                'name', 'SELECT') THEN
        RAISE EXCEPTION '015 verify: app_api kehilangan SELECT kolom biasa';
    END IF;
    IF NOT has_table_privilege('app_api', 'public.source_connections',
                               'DELETE') THEN
        RAISE EXCEPTION '015 verify: app_api kehilangan DELETE';
    END IF;

    -- 4) Fungsi audited tetap bisa dipanggil (jalur baca yang sah).
    IF NOT has_function_privilege('app_api',
                                  'public.get_source_secret(uuid)',
                                  'EXECUTE') THEN
        RAISE EXCEPTION '015 verify: app_api kehilangan EXECUTE get_source_secret';
    END IF;

    RAISE NOTICE '015: grant secret_enc diperbaiki (write-only)';
END
$do$;
