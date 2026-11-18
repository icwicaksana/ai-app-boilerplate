# Migrasi v0.x → v1.0

Panduan upgrade untuk deployment yang jalan di v0.9.x. Estimasi: 30–60 menit
+ verifikasi. Baca juga `docs/runbooks/deploy-rollback.md`.

## 1. Ringkas (checklist)

- [ ] Backup DB (`pg_dump`) + catat `SELECT version FROM schema_migrations`.
- [ ] Update kode ke tag `v1.0` + install lock baru (`prometheus_client`).
- [ ] Isi env baru: `METRICS_TOKEN` (wajib prod), opsional tuning lain.
- [ ] Terapkan migrasi 007–022 berurutan (idempotent).
- [ ] Backfill ACL Qdrant: `python -m app.workers.reindex` (paritas
      content_norm + default `access_level=public` untuk point lama).
- [ ] Verifikasi: login → me (`clearance` terisi) → chat → upload → ready.
- [ ] Smoke eval: `python -m evals.run --all --tags canary` (harus 5/5).
- [ ] Scrape `/metrics` dari Prometheus (Bearer token).

## 2. Perubahan breaking

### 2.1 Kolom `app_users.clearance` (migrasi 022)

- User lama = `public` (fail-closed: dokumen internal tak terlihat).
- Naikkan manual bila perlu:
  `UPDATE app_users SET clearance='internal' WHERE ...;`
- Token lama (tanpa klaim `clearance`) = `public`. User login ulang untuk
  klaim baru (atau tunggu refresh — klaim ikut saat refresh? TIDAK:
  refresh mempertahankan klaim; minta login ulang).

### 2.2 Filter `access_level` wajib di retrieval

- Semua retrieval otomatis memfilter sesuai clearance JWT (intersect —
  pemanggil tak bisa melebarkan).
- Upload: param `access_level` baru (default `public`); upload level di
  atas clearance sendiri = 403.

### 2.3 Env & dependensi

- `METRICS_TOKEN`: kosong = `/metrics` loopback saja. Prod WAJIB isi.
- `prometheus_client==0.26.0` di lock (tanpa dep transitif baru).
- Prompt RAG +1 baris hirarki (hash canary berubah — regenerasi baseline
  bila Anda pin hash prompt sendiri).

### 2.4 API

- Tanpa endpoint dihapus. `/metrics` BARU (root, bukan `/api/v1`).
- Event SSE tak berubah (kontrak R: envelope started/result/error).

## 3. Rollback

Lihat runbook deploy-rollback. Inti: rollback app SAJA aman (kode lama
abaikan kolom clearance). Rollback skema = manual, darurat saja.

## 4. Pasca-migrasi

- Jalankan gold live 1× (gold30 + graph-gold) untuk baseline kualitas
  lingkungan Anda; simpan artefak sebagai baseline (`--record`).
- Isi tabel angka staging di `docs/SLO.md` dari run k6 Anda.
