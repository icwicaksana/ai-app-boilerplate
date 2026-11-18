# Runbook: DB / RLS salah (0 baris misterius, 403 massal)

Gejala: query mengembalikan 0 baris padahal data ada; reindex melaporkan
`pg=0`; atau error `permission denied`/`RLS`.

## 1. Aturan emas

- Role APP (`app`) = NON-bypass + RLS: SET LOCAL `app.current_workspace`
  WAJIB di tiap transaksi (helper `_workspace_conn`). Tanpa GUC = 0 baris
  (by design, bukan bug).
- Tool lintas-workspace (reindex, migrasi, backfill) = role BYPASSRLS/
  superuser. Reindex SEBAGAI role app = DITOLAK LOUD (G3).

## 2. Diagnosis

1. Cek role koneksi: `SELECT current_user, rolsuper, rolbypassrls FROM
   pg_roles WHERE rolname = current_user;`
2. Cek GUC dalam transaksi bermasalah:
   `SHOW app.current_workspace;` → kosong = pemanggil lupa SET LOCAL.
3. Cek policy: `SELECT * FROM pg_policies WHERE tablename IN
   ('chunks','documents','kg_edges','kg_entities');`
4. Cek migrasi: `SELECT version FROM schema_migrations ORDER BY 1;`
   (harap 007–022).

## 3. Kasus umum

| Gejala | Penyebab | Aksi |
|---|---|---|
| reindex `REFUSE ... BYPASSRLS` | jalan sebagai role app | jalankan sebagai migrator |
| Worker 0 chunk | pool tanpa SET LOCAL (bug kode) | laporkan + sertakan trace; hotfix via repo helper |
| `permission denied for table` | GRANT kurang (lihat 008/015) | terapkan migrasi yang kurang |
| Login gagal `clearance` | 022 belum teraplikasi | terapkan 022 (atau restart: self-heal) |

## 4. JANGAN

- Jangan `DISABLE ROW LEVEL SECURITY` / bypass global untuk "memperbaiki
  cepat" — isolasi workspace jebol.
- Jangan berikan BYPASSRLS ke role app.
