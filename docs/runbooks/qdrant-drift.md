# Runbook: Qdrant-drift (PG vs Qdrant selisih)

Gejala: hasil pencarian Qdrant aneh/kurang; reindex `match=false`;
atau setelah migrasi backend.

Prinsip: PG = SoR; Qdrant = turunan rebuildable (D4).

## 1. Ukur drift (kering dulu)

`python -m app.workers.reindex --dry-run` → laporan per workspace:
`pg/qdrant_sebelum/hilang/orphan/match`. `match=false` = drift nyata.

## 2. Klasifikasi

| Laporan | Arti |
|---|---|
| `hilang>0` | point kurang di Qdrant (tulis gagal/terlewat) |
| `orphan>0` | point yatim (dok dihapus PG tapi sisa di Qdrant) |
| `null_vector_skipped>0` | chunk tanpa embedding dilewati (wajar; re-embed bila perlu) |
| `REFUSE pg=0 tapi qdrant=N` | filter/RLS salah — JANGAN lanjut (guard anti-hapus-massal) |
| `REFUSE ... BYPASSRLS` | salah role — pakai migrator |
| `REFUSE ... campur/!= pin` | index PG campur-model — re-embed penuh dulu (G4) |

## 3. Perbaiki

1. `python -m app.workers.reindex --workspace <ws>` (penuh, hapus orphan).
2. Untuk inkremental: `--since <ISO>` (TIDAK hapus orphan — by design).
3. Verifikasi: ulangi `--dry-run` → `match=true` semua workspace.

## 4. JANGAN

- Jangan hapus koleksi Qdrant manual lalu berharap sinkron — pakai reindex
  (paritas content_norm + ACL + skip NULL dijamin di sana).
- Jangan reindex saat ingest besar berjalan (angka bergerak; hasilkan
  false-drift) — jadwalkan di jam sepi atau kunci workspace dulu.
