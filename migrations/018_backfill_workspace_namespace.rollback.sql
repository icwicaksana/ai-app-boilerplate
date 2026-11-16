-- 018_backfill_workspace_namespace.rollback.sql (down untuk 018 — MANUAL,
-- tidak pernah didaftarkan di MIGRATIONS).
--
-- Kembalikan DATA ke label `tenant` supaya kode lama (memori
-- ("tenants",…), ontology WORKSPACE_EXT_PREFIX="tenant:") menemukan datanya.
--
-- Konversi ini KEBALIKAN SIMETRIS dari 018-up, termasuk baris yang ditulis
-- kode BARU setelah 018 (memang harus ikut dikembalikan). Tidak ada data
-- yang hilang selama collision check lolos.
--
-- Jalankan saat TIDAK ada proses kode baru yang hidup, kalau tidak proses
-- itu menulis 'workspace:*' lagi tepat setelah rollback selesai.
--
-- Jalankan dalam transaksi (runner membungkus; psql manual: BEGIN ... COMMIT).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '180s';

-- ===========================================================================
-- A. NAMESPACE MEMORI
-- ===========================================================================
DO $do$
DECLARE
    v_n      INT;
    v_sample TEXT;
    v_con    TEXT;
    v_has_sv BOOLEAN;
BEGIN
    IF to_regclass('public.store') IS NULL THEN
        RAISE NOTICE '018A-down: tabel store belum ada — dilewati';
        RETURN;
    END IF;

    -- Collision (kebalikan 018-up).
    WITH baru AS (
        SELECT prefix, key FROM public.store
         WHERE prefix = 'workspaces' OR starts_with(prefix, 'workspaces.')
    ), dipetakan AS (
        SELECT CASE WHEN n.prefix = 'workspaces' THEN 'tenants'
                    ELSE 'tenants.' || substr(n.prefix, 11) END AS prefix_lama,
               n.key
          FROM baru n
    )
    SELECT count(*), string_agg(DISTINCT format('%s', d.key), ', ')
      INTO v_n, v_sample
      FROM dipetakan d
      JOIN public.store s ON s.prefix = d.prefix_lama AND s.key = d.key;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018A-down: % bentrok (prefix lama & baru sama-sama ada) — '
            'selesaikan manual dulu. Contoh key: %', v_n, v_sample;
    END IF;

    -- store_vectors: FK (prefix,key) tidak ON UPDATE CASCADE -> lepas dulu.
    v_has_sv := to_regclass('public.store_vectors') IS NOT NULL;
    IF v_has_sv THEN
        SELECT c.conname INTO v_con
          FROM pg_constraint c
         WHERE c.conrelid = 'public.store_vectors'::regclass
           AND c.contype = 'f'
           AND c.confrelid = 'public.store'::regclass
         LIMIT 1;
        IF v_con IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.store_vectors DROP CONSTRAINT %I',
                           v_con);
        END IF;
        UPDATE public.store_vectors
           SET prefix = 'tenants' WHERE prefix = 'workspaces';
        UPDATE public.store_vectors
           SET prefix = 'tenants.' || substr(prefix, 11)
         WHERE starts_with(prefix, 'workspaces.');
    END IF;

    UPDATE public.store SET prefix = 'tenants' WHERE prefix = 'workspaces';
    UPDATE public.store
       SET prefix = 'tenants.' || substr(prefix, length('workspaces.') + 1)
     WHERE starts_with(prefix, 'workspaces.');

    IF v_has_sv AND v_con IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.store_vectors ADD CONSTRAINT %I '
            'FOREIGN KEY (prefix, key) REFERENCES public.store(prefix, key) '
            'ON DELETE CASCADE', v_con);
    END IF;

    SELECT count(*) INTO v_n
      FROM public.store
     WHERE prefix = 'workspaces' OR starts_with(prefix, 'workspaces.');
    IF v_n > 0 THEN
        RAISE EXCEPTION '018A-down verify: % baris masih ber-prefix workspaces',
                        v_n;
    END IF;
    RAISE NOTICE '018A-down: namespace memori kembali ke tenants.*';
END
$do$;

-- ===========================================================================
-- B. PREFIX ONTOLOGI (butuh elevation yang sama seperti 018-up: FORCE RLS)
-- ===========================================================================
DO $do$
DECLARE
    v_super      BOOLEAN;
    v_bypass     BOOLEAN;
    v_owner_ent  OID;
    v_owner_edge OID;
    v_force_off  BOOLEAN := FALSE;
    v_n          INT;
    v_sample     TEXT;
BEGIN
    IF to_regclass('public.kg_entities') IS NULL
       OR to_regclass('public.kg_edges') IS NULL THEN
        RAISE EXCEPTION '018B-down preflight: kg_entities/kg_edges tidak ada';
    END IF;

    SELECT r.rolsuper, r.rolbypassrls INTO v_super, v_bypass
      FROM pg_roles r WHERE r.rolname = current_user;

    IF NOT (COALESCE(v_super, FALSE) OR COALESCE(v_bypass, FALSE)) THEN
        SELECT c.relowner INTO v_owner_ent
          FROM pg_class c WHERE c.oid = 'public.kg_entities'::regclass;
        SELECT c.relowner INTO v_owner_edge
          FROM pg_class c WHERE c.oid = 'public.kg_edges'::regclass;
        IF pg_has_role(v_owner_ent, 'MEMBER')
           AND pg_has_role(v_owner_edge, 'MEMBER') THEN
            EXECUTE 'ALTER TABLE public.kg_entities NO FORCE ROW LEVEL SECURITY';
            EXECUTE 'ALTER TABLE public.kg_edges NO FORCE ROW LEVEL SECURITY';
            v_force_off := TRUE;
        ELSE
            RAISE EXCEPTION
                '018B-down preflight: role % tak bisa melewati FORCE RLS '
                '(UPDATE akan 0 baris tanpa error). Jalankan sebagai superuser '
                'atau role BYPASSRLS.', current_user;
        END IF;
    END IF;

    -- Collision edge (relation bagian dari UNIQUE).
    SELECT count(*), string_agg(DISTINCT b.relation, ', ')
      INTO v_n, v_sample
      FROM public.kg_edges a
      JOIN public.kg_edges b
        ON b.document_id = a.document_id
       AND b.src_norm    = a.src_norm
       AND b.dst_norm    = a.dst_norm
       AND b.relation    = 'tenant:' || substr(a.relation, 11)
     WHERE starts_with(a.relation, 'workspace:');
    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018B-down: % edge bentrok saat rename balik. Contoh relasi: %',
            v_n, v_sample;
    END IF;

    UPDATE public.kg_entities
       SET type = 'tenant:' || substr(type, length('workspace:') + 1)
     WHERE starts_with(type, 'workspace:');

    UPDATE public.kg_edges
       SET relation = 'tenant:' || substr(relation, length('workspace:') + 1)
     WHERE starts_with(relation, 'workspace:');

    SELECT count(*) INTO v_n
      FROM public.kg_entities WHERE starts_with(type, 'workspace:');
    IF v_n > 0 THEN
        RAISE EXCEPTION '018B-down verify: % entitas masih workspace:*', v_n;
    END IF;
    SELECT count(*) INTO v_n
      FROM public.kg_edges WHERE starts_with(relation, 'workspace:');
    IF v_n > 0 THEN
        RAISE EXCEPTION '018B-down verify: % edge masih workspace:*', v_n;
    END IF;

    IF v_force_off THEN
        EXECUTE 'ALTER TABLE public.kg_entities ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE public.kg_entities FORCE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE public.kg_edges ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE public.kg_edges FORCE ROW LEVEL SECURITY';
    END IF;

    RAISE NOTICE '018B-down: prefix ontologi kembali ke tenant:*';
END
$do$;

-- Kode aplikasi harus ikut di-rollback di deploy yang sama
-- (memory.namespace_for + ontology.WORKSPACE_EXT_PREFIX), kalau tidak memori
-- tampak kosong dan relasi ekstensi jatuh ke force-fit related_to.
-- Untuk membuat runner menjalankan 018 lagi:
--   DELETE FROM schema_migrations WHERE version = '018';
