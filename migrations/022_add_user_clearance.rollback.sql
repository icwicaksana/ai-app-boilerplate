-- 022_add_user_clearance.rollback.sql (down untuk 022 — MANUAL,
-- tidak pernah didaftarkan di MIGRATIONS).
--
-- EKSASITAS: best-effort sadar — kolom clearance DIDROP; nilai per-user
-- hilang. Rollback kode ke pra-G6 mengabaikan kolom ini, jadi drop aman
-- secara fungsional. Catat nilai dulu bila butuh audit:
--   CREATE TABLE _clearance_backup_022 AS
--     SELECT workspace, username, clearance FROM public.app_users;

ALTER TABLE public.app_users DROP CONSTRAINT IF EXISTS
    app_users_clearance_check;
ALTER TABLE public.app_users DROP COLUMN IF EXISTS clearance;
