-- 019_revoke_public_execute.sql (v0.8.2): cabut EXECUTE dari PUBLIC pada
-- fungsi SECURITY DEFINER.
--
-- TEMUAN (diverifikasi di DB, bukan teori):
--     has_function_privilege('public','public.get_source_secret(uuid)',
--                            'EXECUTE') = true
--     has_function_privilege('public','public.claim_ingest_job(text,int)',
--                            'EXECUTE') = true
--
-- SEBAB: PostgreSQL memberi `EXECUTE` kepada `PUBLIC` secara DEFAULT pada
-- setiap fungsi baru. 007/014 hanya menulis `GRANT EXECUTE ... TO app_api`
-- tanpa pernah `REVOKE ... FROM PUBLIC` — jadi grant itu menambah, bukan
-- membatasi.
--
-- DAMPAK: `get_source_secret` milik `rag_admin` (BYPASSRLS) membaca
-- `secret_enc` untuk SEMUA workspace tanpa filter. Role DB mana pun yang
-- bisa terhubung — termasuk role baca-saja milik tooling/BI/backup —
-- dapat memanggilnya dan membaca seluruh kredensial konektor. Ini melengkapi
-- 015 (yang menutup jalur SELECT langsung) dengan menutup jalur fungsi.
--
-- PERBAIKAN: cabut dari PUBLIC, beri ulang hanya ke role yang memang perlu.
-- Tidak ada DROP/CREATE: signature dan body tidak berubah, jadi tidak ada
-- jendela waktu fungsi hilang.
--
-- Tidak ada BEGIN/COMMIT: runner membungkus satu berkas dalam satu transaksi.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $do$
DECLARE
    v_role TEXT;
BEGIN
    -- 1) get_source_secret(uuid) — jalur baca secret. Wajib tertutup.
    IF to_regprocedure('public.get_source_secret(uuid)') IS NOT NULL THEN
        EXECUTE 'REVOKE ALL ON FUNCTION public.get_source_secret(uuid) '
                'FROM PUBLIC';
        -- Beri ulang ke role aplikasi + role koneksi (nama user di
        -- DATABASE_URL). Grant hanya bila role-nya ada, supaya migrasi ini
        -- tidak gagal di deployment yang memakai nama role berbeda.
        FOREACH v_role IN ARRAY ARRAY['app_api', 'app'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.get_source_secret(uuid) '
                    'TO %I', v_role);
            END IF;
        END LOOP;
    ELSE
        RAISE NOTICE '019: get_source_secret(uuid) tidak ada — dilewati';
    END IF;

    -- 2) claim_ingest_job(text,int) — tidak membaca secret, tapi tetap
    --    SECURITY DEFINER yang menembus RLS. PUBLIC tidak punya alasan
    --    memanggilnya.
    IF to_regprocedure('public.claim_ingest_job(text,int)') IS NOT NULL THEN
        EXECUTE 'REVOKE ALL ON FUNCTION public.claim_ingest_job(text,int) '
                'FROM PUBLIC';
        FOREACH v_role IN ARRAY ARRAY['app_api', 'app'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.claim_ingest_job(text,int) '
                    'TO %I', v_role);
            END IF;
        END LOOP;
    ELSE
        RAISE NOTICE '019: claim_ingest_job(text,int) tidak ada — dilewati';
    END IF;

    -- 3) VERIFIKASI: PUBLIC tidak boleh bisa memanggil keduanya.
    IF to_regprocedure('public.get_source_secret(uuid)') IS NOT NULL
       AND has_function_privilege('public', 'public.get_source_secret(uuid)',
                                  'EXECUTE') THEN
        RAISE EXCEPTION '019 verify: PUBLIC masih bisa EXECUTE get_source_secret';
    END IF;
    IF to_regprocedure('public.claim_ingest_job(text,int)') IS NOT NULL
       AND has_function_privilege('public', 'public.claim_ingest_job(text,int)',
                                  'EXECUTE') THEN
        RAISE EXCEPTION '019 verify: PUBLIC masih bisa EXECUTE claim_ingest_job';
    END IF;

    -- 4) Jalur sah harus tetap hidup: app_api bisa memanggil keduanya.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
        IF NOT has_function_privilege('app_api',
                                      'public.get_source_secret(uuid)',
                                      'EXECUTE') THEN
            RAISE EXCEPTION '019 verify: app_api kehilangan get_source_secret';
        END IF;
    END IF;

    RAISE NOTICE '019: EXECUTE PUBLIC dicabut dari fungsi SECURITY DEFINER';
END
$do$;
