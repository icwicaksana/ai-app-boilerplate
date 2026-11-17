-- 021_backfill_kg_edge_acl.rollback.sql (down untuk 021 — MANUAL,
-- tidak pernah didaftarkan di MIGRATIONS).
--
-- EKSASITAS: rollback ini EKSAK (bukan best-effort). Pra-021, 100% edge
-- tertulis 'public' — worker memakai `e.get("access_level") or acl` dan
-- semua ekstraktor meng-hardcode "public". Tak ada edge non-public yang
-- perlu "dikembalikan". Baris yang ditulis kode BARU pasca-021 (dengan ACL
-- SoR) ikut kembali ke 'public' — benar untuk kode lama (yang menganggap
-- semua public).
--
-- Jalankan dalam transaksi. Butuh superuser/BYPASSRLS (FORCE RLS, 011).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '180s';

DO $do$
DECLARE
    v_super  BOOLEAN;
    v_bypass BOOLEAN;
    v_n      INT;
BEGIN
    SELECT r.rolsuper, r.rolbypassrls INTO v_super, v_bypass
      FROM pg_roles r WHERE r.rolname = current_user;
    IF NOT (COALESCE(v_super, FALSE) OR COALESCE(v_bypass, FALSE)) THEN
        RAISE EXCEPTION '021-down: butuh superuser/BYPASSRLS (FORCE RLS).';
    END IF;
    UPDATE public.kg_edges SET access_level = 'public'
     WHERE access_level IS DISTINCT FROM 'public';
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RAISE NOTICE '021-down: % edge dikembalikan ke public', v_n;
END
$do$;
