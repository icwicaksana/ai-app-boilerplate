# Runbook: ingest-stuck (dokumen tak kunjung ready)

Gejala: `GET /documents/{id}` status `processing` > 10 menit, atau
`failed`/`dead` massal.

## 1. Baca status jujur

1. `GET /documents/{id}` → `status`, `last_error`, `job_id`.
2. `GET /ingestion_jobs/{job_id}` → `state`, `stage`, `progress`,
   `claimed_by`, `lease_until`.
3. `GET /documents/{id}/progress` → SSE progres (bila masih jalan).

## 2. Klasifikasi

| Tanda | Arti | Aksi |
|---|---|---|
| `failed` + `last_error` jelas (OCR/parse/embed) | gagal tahap | perbaiki input/layanan → `POST .../retry` |
| `dead` (retry habis) | racun permanen | inspeksi blob → hapus/retry manual |
| `processing`, lease segar, stage maju | normal lambat (dok besar/OCR) | tunggu; SLO 90 dtk untuk ≤1 MB |
| `processing`, lease basi (>5 mnt), worker hidup | worker macet/crash | restart worker (job direbut via lease) |
| `processing`, lease basi, worker MATI | tak ada yang kerja | start worker |
| `queued` lama | antre menumpuk / worker kurang | tambah worker / cek DB |

## 3. Perintah kunci

- Retry 1 job: `POST /api/v1/ingestion_jobs/{job_id}/retry` (202).
- Hapus dokumen + job: `DELETE /api/v1/documents/{id}` (202; best-effort
  dua sisi + ringkasan hasil hapus per-sisi).
- Cek worker: log `app.workers.ingest` (klaim/heartbeat per 2 mnt).

## 4. JANGAN

- Jangan `DELETE` lalu upload ulang tanpa baca `last_error` (penyebab sama
  akan gagal lagi).
- Jangan jalankan 2 worker dengan `worker_id` sama (fencing lease kacau).
