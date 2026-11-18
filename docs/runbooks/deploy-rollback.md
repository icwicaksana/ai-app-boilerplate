# Runbook: deploy & rollback

## 1. Pra-deploy (wajib)

- [ ] `pytest tests/ -m "not integration"` hijau di CI.
- [ ] `scripts/rename_audit --guard` bersih (CI).
- [ ] Image: `docker build` dari lock (`requirements.lock`), tag versi.
- [ ] Cek migrasi baru: `SELECT version FROM schema_migrations` di staging
      vs daftar `migrations/*.sql` — terapkan berurutan.
- [ ] `.env` staging lengkap (lihat `.env.example`; `JWT_SECRET` ≥32,
      `METRICS_TOKEN` diisi).

## 2. Deploy (rolling, 1 replika dulu)

1. Terapkan migrasi: `python -m app.infra.rag_migrate` (idempotent;
   advisory lock anti dobel).
2. Start replika baru → tunggu `/ready` 200.
3. Cek `/metrics` (scrape OK) + dashboard RED tanpa 5xx 5 menit.
4. Lanjut replika berikut.

## 3. Rollback

- App: kembalikan image ke tag sebelumnya → rolling restart.
  Kode v1.0 kompatibel baca-skema-lama KECUALI kolom baru dipakai tulis:
  - `022` (clearance): kode lama abaikan kolom → rollback app SAJA aman.
  - Rollback SKEMA: jalankan `migrations/*.rollback.sql` MANUAL berurutan
    mundur (down script; KHUSUS darurat — baca header EKSASITAS dulu).
- JANGAN rollback skema bila data baru sudah ditulis kolom baru tanpa
  backup (`_clearance_backup_022` dsb).

## 4. Pasca-deploy

- Smoke: login → me → 1 chat SSE → 1 upload md → ready.
- Catat versi + waktu di CHANGELOG ops.
