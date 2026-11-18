-- 022_add_user_clearance.sql — kolom clearance untuk fail-closed G6.
--
-- LATAR: G6 (V1-D/2) menambah clearance ke klaim JWT + app_users, tapi
-- app_users dibuat oleh users.ensure_schema (CREATE TABLE IF NOT EXISTS,
-- tanpa jalur ALTER) — DB lama TAK PERNAH dapat kolomnya. Login/me live
-- gagal (UndefinedColumn). Temuan probe beban V1-F.
--
-- Aman diulang (IF NOT EXISTS); default 'public' = fail-closed untuk
-- user lama. Butuh hak ALTER (role migrator).

ALTER TABLE public.app_users
    ADD COLUMN IF NOT EXISTS clearance TEXT NOT NULL DEFAULT 'public';

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_clearance_check') THEN
        ALTER TABLE public.app_users
            ADD CONSTRAINT app_users_clearance_check
            CHECK (clearance IN ('public', 'internal', 'restricted'));
    END IF;
END
$do$;

COMMENT ON COLUMN public.app_users.clearance IS
    'v1.0 G6: clearance dokumen user (public<internal<restricted).';
