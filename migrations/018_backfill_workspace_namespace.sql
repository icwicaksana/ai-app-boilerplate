-- 018_backfill_workspace_namespace.sql (v0.8.2, Tier C #3): MIGRASI DATA
-- dua nilai tersimpan yang masih berlabel `tenant`.
--
--   A. LangGraph PostgresStore: prefix namespace memori
--        'tenants.<workspace>.users.<user>'  ->  'workspaces.<workspace>.users.<user>'
--      (tabel `store`, kolom `prefix`; kunci = (prefix, key))
--
--   B. Prefix ontologi KG yang TERSIMPAN (bukan nama simbol):
--        kg_entities.type      'tenant:xxx' -> 'workspace:xxx'
--        kg_edges.relation     'tenant:xxx' -> 'workspace:xxx'
--
-- TIDAK termasuk (sengaja): klaim JWT `tenant`, format thread
-- `tenant:user:session`, payload Qdrant `key="tenant"`, kode error
-- `CROSS_TENANT_DENIED`, kolom DB (sudah 014), GUC (016), OUT param (017).
--
-- ---------------------------------------------------------------------------
-- 0. VERIFIKASI SKEMA `store` — JANGAN DILEWATI
-- ---------------------------------------------------------------------------
-- Skema sebenarnya (langgraph 1.2.15, langgraph/store/postgres/base.py,
-- MIGRATIONS[0..3]) — dibuat oleh `store.setup()` di lifespan, BUKAN oleh
-- migrasi 001-015:
--     CREATE TABLE store (
--         prefix text NOT NULL,          -- namespace, lihat di bawah
--         key    text NOT NULL,
--         value  jsonb NOT NULL,
--         created_at timestamptz DEFAULT CURRENT_TIMESTAMP,
--         updated_at timestamptz DEFAULT CURRENT_TIMESTAMP,
--         expires_at timestamptz, ttl_minutes int,   -- migrasi v2/v3
--         PRIMARY KEY (prefix, key));
--     CREATE INDEX store_prefix_idx ON store (prefix text_pattern_ops);
--
-- FORMAT `prefix` = SEGMEN DIJOIN TITIK, BUKAN GARIS MIRING:
--     _namespace_to_text(ns) = ".".join(ns)          (base.py:1279)
--     _decode_ns_bytes(s)    = tuple(s.split("."))   (base.py:1408)
-- Jadi namespace 4-tuple dari app/infra/memory.py `namespace_for()`
--     ("tenants", t, "users", u)
-- tersimpan sebagai:  'tenants.toko-a.users.budi'
--
-- KONSEKUENSI (jebakan paling mahal di migrasi ini): siapa pun yang menulis
--     UPDATE store SET prefix = 'workspaces/' || substr(prefix, 12)
--                   WHERE prefix LIKE 'tenants/%'
-- akan mengenai **0 baris** dan melaporkan sukses. Aplikasi lalu membaca
-- prefix baru yang kosong -> memori user tampak HILANG (padahal datanya
-- masih di sana, cuma tak terlihat). Fail-closed-nya bagus, silent-nya yang
-- mematikan. Notasi `tenants/{t}/users/{u}` di README adalah prosa (kurung
-- kurawal = variabel), bukan nilai on-disk.
--
-- Tidak ada escaping: segmen berisi '.' akan ambigu. Aman untuk kasus ini
-- karena canon_segment() membatasi charset (WORKSPACE_RE ^[a-z0-9-]{3,32}$,
-- USER_RE ^[a-z0-9-_]{1,64}$ — tanpa titik). Verifikasi di bawah menolak DB
-- yang ternyata memakai separator lain.
--
-- ---------------------------------------------------------------------------
-- 0b. FORCE RLS: kenapa 018 GAGAL SENYAP TANPA role yang tepat
-- ---------------------------------------------------------------------------
-- kg_entities & kg_edges adalah `FORCE ROW LEVEL SECURITY` (011). FORCE
-- berarti policy BERLAKU JUGA bagi table OWNER (hanya superuser dan role
-- BYPASSRLS yang lepas). Maka `UPDATE kg_entities SET type = ...` dari role
-- aplikasi (owner) mengevaluasi policy `workspace = COALESCE(...)` dengan
-- GUC kosong -> NULL -> **0 baris ter-update, tanpa error**. Sama untuk
-- setiap SELECT verifikasinya (0 baris = "terlihat bersih").
--
-- Tiga jalur yang diterima, dicek berurutan:
--   1. current_user superuser / BYPASSRLS  -> UPDATE langsung.
--   2. current_user member role pemilik tabel -> `ALTER TABLE ... NO FORCE
--      ROW LEVEL SECURITY` sementara (atomik: ACCESS EXCLUSIVE menahan DML
--      lain sampai COMMIT, dan FORCE dikembalikan sebelum COMMIT; kalau
--      gagal, transaksi rollback = FORCE utuh).
--   3. selain itu -> GAGAL KERAS dengan instruksi (bukan 0-baris senyap).
--
-- CATATAN TERUJI (penting, jangan salah paham soal jalur 2): jalur 2 praktis
-- TIDAK terpakai, karena rantai 007+ SUDAH menuntut migrator berupa
-- superuser/BYPASSRLS jauh sebelum 018:
--   * 007 `CREATE FUNCTION get_source_secret ... SET row_security = OFF`
--     gagal bila pembuatnya tunduk RLS:
--         ERROR: query would be affected by row-level security policy for
--                table "source_connections"
--         CONTEXT: SQL function "get_source_secret"
--     (diverifikasi di PG 16.15: `check_function_bodies` waktu CREATE
--     memvalidasi body SQL sebagai pembuatnya.)
--   * `ALTER FUNCTION ... OWNER TO rag_admin` (007/014/017) menuntut
--     `rag_admin` punya CREATE di schema `public`.
-- Jadi jalur 2 adalah jaring pengaman bila suatu saat 007 diganti pola
-- least-privilege (mis. fungsi dibuat oleh rag_admin sendiri) — bukan jalur
-- normal hari ini. Yang WAJIB hari ini: jangan sampai jalur 3 terjadi.
--
-- (Alternatif "enumerasi workspace lalu SET LOCAL per workspace" TIDAK
-- mungkin di sini: daftar workspace hanya ada di tabel-tabel ber-RLS.
-- app_users/audit_log memang tanpa RLS, tapi tidak lengkap sebagai sumber —
-- jangan dipakai.)
--
-- ---------------------------------------------------------------------------
-- URUTAN & IDEMPOTENSI
-- ---------------------------------------------------------------------------
--   A. store   (tanpa RLS, murah) — preflight format -> collision -> UPDATE
--   B. KG      (butuh elevation) — preflight -> collision -> UPDATE x2 -> cek
-- Setiap UPDATE bersyarat `starts_with(...)` sehingga re-run = no-op.
-- Collision dicek SEBELUM UPDATE; kalau ada, GAGAL KERAS (lihat komentar).
--
-- ROLLBACK: migrations/018_backfill_workspace_namespace.rollback.sql

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '180s';

-- ===========================================================================
-- A. NAMESPACE MEMORI (`store.prefix`)
-- ===========================================================================
DO $do$
DECLARE
    v_n      INT;
    v_sample TEXT;
    v_con    TEXT;
    v_has_sv BOOLEAN;
BEGIN
    IF to_regclass('public.store') IS NULL THEN
        -- SAH: worker memanggil migrasi TANPA `store.setup()` (beda dengan
        -- API yang memanggil setup_persistence lebih dulu — app/main.py:111
        -- vs app/workers/ingest.py:155). Di DB baru yang di-boot worker
        -- dulu, tabel `store` belum ada. Tidak ada yang perlu dimigrasi.
        RAISE NOTICE '018A: tabel store belum ada (setup langgraph belum '
                     'jalan) — dilewati';
        RETURN;
    END IF;

    -- (a) Skema persis seperti yang kita asumsikan.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
         WHERE a.attrelid = 'public.store'::regclass
           AND a.attname = 'prefix' AND a.atttypid = 'text'::regtype
           AND a.attnum > 0 AND NOT a.attisdropped
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_attribute a
         WHERE a.attrelid = 'public.store'::regclass
           AND a.attname = 'key' AND a.atttypid = 'text'::regtype
           AND a.attnum > 0 AND NOT a.attisdropped
    ) THEN
        RAISE EXCEPTION
            '018A preflight: skema store tak seperti asumsi (prefix/key text) — '
            'versi langgraph berbeda? Periksa langgraph/store/postgres/base.py '
            'MIGRATIONS sebelum melanjutkan';
    END IF;

    -- (b) Format separator: tolak DB yang memakai '/' (asumsi salah -> kalau
    --     diteruskan, migrasi ini akan "sukses" dengan 0 baris).
    SELECT count(*) INTO v_n FROM public.store WHERE prefix LIKE 'tenants/%';
    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018A preflight: % baris memakai separator "/" (tenants/...) — '
            'encoder namespace berbeda dari asumsi titik; HENTIKAN dan '
            'periksa versi langgraph', v_n;
    END IF;

    -- (c) Collision: kunci = (prefix, key). Kalau sebuah key sudah ada di
    --     prefix baru (kode baru menulis lebih dulu), UPDATE akan melanggar
    --     PK. GAGAL KERAS: merge nilai memori (profil dict / teks fakta)
    --     tidak bisa ditebak mesin — keputusan manusia.
    WITH lama AS (
        SELECT prefix, key FROM public.store
         WHERE prefix = 'tenants' OR starts_with(prefix, 'tenants.')
    ), dipetakan AS (
        SELECT CASE WHEN l.prefix = 'tenants' THEN 'workspaces'
                    ELSE 'workspaces.' || substr(l.prefix, 9) END AS prefix_baru,
               l.key
          FROM lama l
    )
    SELECT count(*), string_agg(DISTINCT format('%s', d.key), ', ')
      INTO v_n, v_sample
      FROM dipetakan d
      JOIN public.store s ON s.prefix = d.prefix_baru AND s.key = d.key;

    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018A: % bentrok (prefix lama & baru sama-sama ada) — selesaikan '
            'manual dulu. Contoh key: %', v_n, v_sample;
    END IF;

    -- (d) `store_vectors` (hanya ada bila store dibuat dengan index_config;
    --     repo ini memanggil AsyncPostgresStore(pool) TANPA index_config,
    --     jadi normalnya tidak ada). FK-nya (prefix, key) REFERENCES
    --     store(prefix, key) ON DELETE CASCADE dan TIDAK `ON UPDATE CASCADE`
    --     serta TIDAK deferrable -> UPDATE parent lebih dulu PASTI gagal
    --     ("violates foreign key constraint"). Jadi: lepas FK, update
    --     keduanya, pasang lagi (tabelnya kecil, ACCESS EXCLUSIVE-nya
    --     sekejap; semuanya dalam satu transaksi).
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
           SET prefix = 'workspaces' WHERE prefix = 'tenants';
        UPDATE public.store_vectors
           SET prefix = 'workspaces.' || substr(prefix, 9)
         WHERE starts_with(prefix, 'tenants.');
    END IF;

    -- (e) UPDATE. Idempoten: setelah jalan, tak ada lagi prefix 'tenants.*'.
    SELECT count(*) INTO v_n
      FROM public.store
     WHERE prefix = 'tenants' OR starts_with(prefix, 'tenants.');
    UPDATE public.store SET prefix = 'workspaces' WHERE prefix = 'tenants';
    UPDATE public.store
       SET prefix = 'workspaces.' || substr(prefix, length('tenants.') + 1)
     WHERE starts_with(prefix, 'tenants.');
    RAISE NOTICE '018A: % baris store dipindah ke prefix workspaces.*', v_n;

    -- (f) Pasang lagi FK (validasi penuh — kalau ada anak yatim, ini gagal,
    --     dan itu memang yang kita mau ketahui).
    IF v_has_sv AND v_con IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.store_vectors ADD CONSTRAINT %I '
            'FOREIGN KEY (prefix, key) REFERENCES public.store(prefix, key) '
            'ON DELETE CASCADE', v_con);
    END IF;

    -- (g) Verifikasi: TIDAK boleh ada prefix yang SEGMEN PERTAMANYA 'tenants'.
    --     (Segmen 'tenants' di posisi lain LEGAL — mis. user bernama
    --     "tenants1" — jadi jangan dicocokkan dengan LIKE '%tenants%'.)
    SELECT count(*) INTO v_n
      FROM public.store
     WHERE prefix = 'tenants' OR starts_with(prefix, 'tenants.');
    IF v_n > 0 THEN
        RAISE EXCEPTION '018A verify: % baris masih ber-prefix tenants', v_n;
    END IF;

    -- (h) Bentuk hasil: harus 'workspaces.<3+ segmen>'.
    SELECT count(*) INTO v_n
      FROM public.store
     WHERE starts_with(prefix, 'workspaces.')
       AND array_length(string_to_array(prefix, '.'), 1) < 4;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018A verify: % baris prefix workspaces.* tak berbentuk '
            'workspaces.<ws>.users.<user>', v_n;
    END IF;

    RAISE NOTICE '018A: namespace memori selesai & terverifikasi';
END
$do$;

-- ===========================================================================
-- B. PREFIX ONTOLOGI TERSIMPAN (`kg_entities.type`, `kg_edges.relation`)
-- ===========================================================================
-- Nilai `tenant:xxx` adalah NILAI DATA, bukan nama kolom/simbol: ia ikut
-- jadi bagian UNIQUE kg_edges (document_id, src_norm, dst_norm, relation),
-- jadi rename nilai BUKAN metadata-only — ini UPDATE sungguhan.
--
-- ONTO_VERSION TIDAK di-bump (tetap 'kg_onto/v1'). Alasannya konkret:
-- `resolve_seeds`, `_seed_lookup`, dan `_level` (pg_graph.py) memfilter
-- `onto_version = %s`. Bump akan membuat SELURUH baris lama tak terlihat
-- oleh query baru -> KG mati sampai reindex penuh. Rename prefix namespace
-- bukan perubahan kosakata; v1 tetap benar.
DO $do$
DECLARE
    v_super      BOOLEAN;
    v_bypass     BOOLEAN;
    v_owner_ent  OID;
    v_owner_edge OID;
    v_force_off  BOOLEAN := FALSE;
    v_n          INT;
    v_n2         INT;
    v_sample     TEXT;
BEGIN
    IF to_regclass('public.kg_entities') IS NULL
       OR to_regclass('public.kg_edges') IS NULL THEN
        RAISE EXCEPTION
            '018B preflight: kg_entities/kg_edges tidak ada — 011 belum jalan';
    END IF;

    -- (a) Elevation check (lihat 0b di header).
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
            RAISE NOTICE '018B: role % bukan superuser/BYPASSRLS — FORCE RLS '
                         'dimatikan sementara (dikembalikan sebelum COMMIT)',
                         current_user;
        ELSE
            RAISE EXCEPTION
                '018B preflight: role % tidak bisa melewati FORCE RLS pada '
                'kg_entities/kg_edges (bukan superuser, tanpa BYPASSRLS, bukan '
                'owner). UPDATE akan mengenai 0 baris TANPA error. Jalankan '
                'migrasi sebagai superuser, atau beri BYPASSRLS pada role '
                'migrasi, atau jalankan manual: ALTER TABLE kg_entities NO '
                'FORCE ROW LEVEL SECURITY; (dst) lalu ulangi.',
                current_user;
        END IF;
    END IF;

    -- (b) Collision EDGE: `relation` bagian dari UNIQUE
    --     (document_id, src_norm, dst_norm, relation). Entity TIDAK perlu
    --     dicek: `type` tidak ada di kunci UNIQUE mana pun.
    SELECT count(*), string_agg(DISTINCT b.relation, ', ')
      INTO v_n, v_sample
      FROM public.kg_edges a
      JOIN public.kg_edges b
        ON b.document_id = a.document_id
       AND b.src_norm    = a.src_norm
       AND b.dst_norm    = a.dst_norm
       AND b.relation    = 'workspace:' || substr(a.relation, 8)
     WHERE starts_with(a.relation, 'tenant:');
    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018B: % edge bentrok saat rename relation (baris lama & baru '
            'sama-sama ada untuk triple yang sama). Contoh relasi: %. '
            'Selesaikan manual: hapus salah satu duplikat.', v_n, v_sample;
    END IF;

    -- (c) UPDATE entity type.
    SELECT count(*) INTO v_n
      FROM public.kg_entities WHERE starts_with(type, 'tenant:');
    UPDATE public.kg_entities
       SET type = 'workspace:' || substr(type, length('tenant:') + 1)
     WHERE starts_with(type, 'tenant:');
    RAISE NOTICE '018B: % entitas: type tenant:* -> workspace:*', v_n;

    -- (d) UPDATE edge relation.
    SELECT count(*) INTO v_n2
      FROM public.kg_edges WHERE starts_with(relation, 'tenant:');
    UPDATE public.kg_edges
       SET relation = 'workspace:' || substr(relation, length('tenant:') + 1)
     WHERE starts_with(relation, 'tenant:');
    RAISE NOTICE '018B: % edge: relation tenant:* -> workspace:*', v_n2;

    -- (e) Verifikasi (masih elevated — kalau tidak, SELECT-nya buta).
    SELECT count(*) INTO v_n
      FROM public.kg_entities WHERE starts_with(type, 'tenant:');
    IF v_n > 0 THEN
        RAISE EXCEPTION '018B verify: % entitas masih bertipe tenant:*', v_n;
    END IF;
    SELECT count(*) INTO v_n
      FROM public.kg_edges WHERE starts_with(relation, 'tenant:');
    IF v_n > 0 THEN
        RAISE EXCEPTION '018B verify: % edge masih berelasi tenant:*', v_n;
    END IF;

    -- Cek bentuk: prefix ekstensi harus diikuti nama non-kosong (sama dengan
    -- is_workspace_extension(): non-kosong, tanpa spasi, tanpa '/').
    SELECT count(*) INTO v_n
      FROM public.kg_entities
     WHERE starts_with(type, 'workspace:')
       AND (char_length(type) <= length('workspace:')
            OR type LIKE '% %' OR type LIKE '%/%');
    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018B verify: % entitas "workspace:" tanpa nama valid', v_n;
    END IF;
    SELECT count(*) INTO v_n
      FROM public.kg_edges
     WHERE starts_with(relation, 'workspace:')
       AND (char_length(relation) <= length('workspace:')
            OR relation LIKE '% %' OR relation LIKE '%/%');
    IF v_n > 0 THEN
        RAISE EXCEPTION
            '018B verify: % edge "workspace:" tanpa nama valid', v_n;
    END IF;

    -- (f) Kembalikan FORCE RLS (jalur sukses). Kalau ada EXCEPTION di atas,
    --     transaksi rollback dan FORCE tetap utuh — dua-duanya benar.
    IF v_force_off THEN
        EXECUTE 'ALTER TABLE public.kg_entities ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE public.kg_entities FORCE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE public.kg_edges ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE public.kg_edges FORCE ROW LEVEL SECURITY';
    END IF;

    RAISE NOTICE '018B: prefix ontologi selesai & terverifikasi';
END
$do$;

-- Verifikasi akhir RLS (di luar blok elevated): FORCE harus hidup lagi.
DO $do$
DECLARE
    v_n INT;
BEGIN
    SELECT count(*) INTO v_n
      FROM pg_class c
     WHERE c.oid = ANY (ARRAY[to_regclass('public.kg_entities'),
                              to_regclass('public.kg_edges')])
       AND c.relrowsecurity AND c.relforcerowsecurity;
    IF v_n <> 2 THEN
        RAISE EXCEPTION '018 verify: RLS enabled+forced hanya % dari 2 tabel KG',
                        v_n;
    END IF;
    RAISE NOTICE '018: data migration selesai';
END
$do$;

-- ============================================================================
-- CO-DEPLOY (WAJIB, commit yang sama — kalau tidak, tulis-baca terbelah):
-- 1. app/infra/rag_migrate.py: ("018", "018_backfill_workspace_namespace.sql").
-- 2. app/infra/memory.py: namespace_for() -> ("workspaces", t, "users", u).
--    (docstring "BEKU" ikut diperbarui; prefix ON-DISK sudah dimigrasi 018.)
-- 3. app/rag/ontology.py: WORKSPACE_EXT_PREFIX = "workspace:".
--    Komentar "DIBEKUKAN sampai v0.8.2" di atasnya ikut dihapus/diperbarui.
-- 4. tests/test_frozen_contract.py: DUA test mengunci keadaan lama dan AKAN
--    MERAH kalau tidak diperbarui di commit yang sama:
--      - test_namespace_memori_masih_tenants  (assert "tenants" di memory.py)
--      - test_nilai_prefix_ontologi_masih_tenant (assert prefix == "tenant:")
-- 5. scripts/rename_audit.py FROZEN_ALLOWLIST: entri "namespace_memori,
--    "prefix_ontologi", dan "komentar_prefix" boleh dihapus SETELAH 2-4.
--    Guard `app/` akan menangkap sisa istilah lama — itu memang tujuannya.
-- 6. Qdrant payload `key="tenant"` TIDAK ikut (file terpisah, butuh
--    re-payload/scroll — di luar 016-018).
--
-- URUTAN DEPLOY (penting, karena 018 tidak bisa "dual-read"):
--   018 mengubah NILAI yang dipakai sebagai kunci baca. Tidak ada pola
--   COALESCE untuk data. Karena itu 018 harus berjalan saat TIDAK ADA proses
--   kode-lama yang hidup:
--     stop worker -> stop API -> jalankan satu proses baru (API, migrasi
--     jalan di startup) -> smoke test (memori user terbaca, KG entity
--     ber-type workspace:xxx) -> start worker.
--   Kalau ada proses lama yang menulis SETELAH 018: baris memori baru masuk
--   ke 'tenants.*' (tak terlihat kode baru) dan edge baru ber-relation
--   'tenant:*' (di-force-fit ke related_to saat reindex). Keduanya senyap.
--   Karena itu 016 (aman dual-read) boleh rilis lebih dulu; 017+018 satu
--   rilis dengan kode.
-- ============================================================================
