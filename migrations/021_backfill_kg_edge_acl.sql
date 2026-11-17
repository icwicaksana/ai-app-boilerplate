-- 021_backfill_kg_edge_acl.sql (v1.0 G1): MIGRASI DATA — selaraskan
-- kg_edges.access_level dengan SoR (chunk/dokumen).
--
-- Latar: pra-G1, worker memakai `e.get("access_level") or acl` dan SEMUA
-- ekstraktor meng-hardcode "public" — sehingga 100% edge tertulis public,
-- termasuk edge dari dokumen internal/restricted (BOCOR via filter graph).
-- Pasca-G1, worker selalu menulis ACL chunk. Migrasi ini memperbaiki baris
-- lama:
--   * edge ber-chunk_row_id → COALESCE(chunks.meta->>'access_level',
--     'public') dari chunk asalnya;
--   * edge tanpa chunk_row_id (legasi) → COALESCE(documents.meta->
--     'access_level', 'public') dari dokumennya.
--
-- Idempoten: UPDATE bersyarat (hanya yang beda). Aman re-run.
-- ROLLBACK: migrations/021_backfill_kg_edge_acl.rollback.sql (eksak, karena
-- pra-021 semua edge = 'public' — lihat penjelasan di berkas itu).
--
-- Elevation: kg_edges FORCE RLS (011); migrator WAJIB superuser/BYPASSRLS
-- (sudah dituntut 007 — lihat header 018 §0b). Tanpa itu: GAGAL KERAS
-- (bukan 0-baris senyap).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '180s';

DO $do$
DECLARE
    v_super  BOOLEAN;
    v_bypass BOOLEAN;
    v_n      INT;
    v_n2     INT;
BEGIN
    IF to_regclass('public.kg_edges') IS NULL
       OR to_regclass('public.chunks') IS NULL
       OR to_regclass('public.documents') IS NULL THEN
        RAISE EXCEPTION
            '021 preflight: kg_edges/chunks/documents tidak lengkap — '
            '007/011 belum jalan?';
    END IF;

    SELECT r.rolsuper, r.rolbypassrls INTO v_super, v_bypass
      FROM pg_roles r WHERE r.rolname = current_user;
    IF NOT (COALESCE(v_super, FALSE) OR COALESCE(v_bypass, FALSE)) THEN
        RAISE EXCEPTION
            '021 preflight: role % tidak bisa melewati FORCE RLS pada '
            'kg_edges (bukan superuser/BYPASSRLS). UPDATE akan mengenai 0 '
            'baris TANPA error. Jalankan migrasi sebagai superuser atau beri '
            'BYPASSRLS pada role migrasi.', current_user;
    END IF;

    -- (a) Edge ber-chunk: ACL dari chunk asalnya.
    WITH benar AS (
        SELECT e.id AS eid,
               COALESCE(c.meta->>'access_level', 'public') AS acl
          FROM public.kg_edges e
          JOIN public.chunks c ON c.id = e.chunk_row_id
         WHERE e.chunk_row_id IS NOT NULL
    )
    UPDATE public.kg_edges e
       SET access_level = b.acl
      FROM benar b
     WHERE e.id = b.eid AND e.access_level IS DISTINCT FROM b.acl;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RAISE NOTICE '021: % edge ber-chunk diselaraskan dari chunks.meta', v_n;

    -- (b) Edge tanpa chunk_row_id: ACL dari dokumennya.
    WITH benar AS (
        SELECT e.id AS eid,
               COALESCE(d.meta->>'access_level', 'public') AS acl
          FROM public.kg_edges e
          JOIN public.documents d ON d.id = e.document_id
         WHERE e.chunk_row_id IS NULL
    )
    UPDATE public.kg_edges e
       SET access_level = b.acl
      FROM benar b
     WHERE e.id = b.eid AND e.access_level IS DISTINCT FROM b.acl;
    GET DIAGNOSTICS v_n2 = ROW_COUNT;
    RAISE NOTICE '021: % edge tanpa-chunk diselaraskan dari documents.meta', v_n2;

    -- (c) Verifikasi: tak ada edge yang menyimpang dari SoR-nya.
    SELECT count(*) INTO v_n
      FROM public.kg_edges e
      JOIN public.chunks c ON c.id = e.chunk_row_id
     WHERE e.access_level IS DISTINCT FROM
           COALESCE(c.meta->>'access_level', 'public');
    IF v_n > 0 THEN
        RAISE EXCEPTION '021 verify: % edge ber-chunk masih menyimpang', v_n;
    END IF;
    SELECT count(*) INTO v_n
      FROM public.kg_edges e
      JOIN public.documents d ON d.id = e.document_id
     WHERE e.chunk_row_id IS NULL
       AND e.access_level IS DISTINCT FROM
           COALESCE(d.meta->>'access_level', 'public');
    IF v_n > 0 THEN
        RAISE EXCEPTION '021 verify: % edge tanpa-chunk masih menyimpang', v_n;
    END IF;

    RAISE NOTICE '021: backfill ACL edge selesai & terverifikasi';
END
$do$;

-- ============================================================================
-- CO-DEPLOY (WAJIB, commit yang sama):
-- 1. app/infra/rag_migrate.py: ("021", "021_backfill_kg_edge_acl.sql").
-- 2. app/workers/ingest.py: edge ACL selalu dari chunk (SoR).
-- 3. app/infra/rag/extract/*.py: TANPA access_level di output.
-- 4. app/infra/rag/pg_graph.py: fetch provenance terfilter + drop path.
-- URUTAN DEPLOY: stop worker -> stop API -> satu proses baru (migrasi di
-- startup) -> smoke (edge internal tak terlihat filter public) -> start.
-- Kode lama menulis SETELAH 021: edge baru 'public' lagi (tulis ulang
-- saat re-ingest pasca-deploy memperbaikinya; jendela = deploy saja).
-- ============================================================================
