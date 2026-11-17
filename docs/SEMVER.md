# Kebijakan versi & kompatibilitas (v1.0)

## 1. Skema

Tag repo: `v0`, `v0.1`, …, `v0.9.0`, `v1.0` (bukan semver 3-angka —
konsisten histori; K2). API HTTP: `/api/v1` (beku kompatibel).
`APP_VERSION` di `app/core/config.py` = satu sumber, wajib sama dengan tag.

## 2. Janji kompatibilitas v1.x

Klien yang hanya memakai **kontrak publik** (envelope SSE/WS, RFC7807,
skema request/response `/api/v1`, format thread-ID, kode error
terdokumentasi) tidak akan rusak oleh rilis v1.x. Yang di luar daftar
itu = internal, boleh berubah tanpa pemberitahuan.

## 3. Perubahan breaking

Hanya di rilis mayor berikutnya (v2). Tidak ada breaking diam-diam di
minor/patch — preseden v0.8.2→v0.8.3 (memutus token lama) dinyatakan
 TIDAK BOLEH terulang (saat itu belum ada fork produksi; sekarang ada).

## 4. Deprecation

Tandai `@deprecated` + catat di CHANGELOG + umumkan minimal 1 rilis minor
sebelum pencabutan. Sunset minimal 90 hari untuk kontrak publik.

## 5. Disiplin tag

Rilis = tag annotated (`git tag -a vX -m ...`) + push tag. Jangan
force-push tag yang sudah diambil fork. Nomor versi naik tepat 1 langkah
per rilis (tidak ada `v1.0.1` tanpa `v1.0`).

## 6. Sinkronisasi wajib tiap rilis

`APP_VERSION` = tag. `.env.example` (tanpa drift versi), README endpoint,
CHANGELOG entri, `Docs/ROADMAP.md` (peta + detail + riwayat) diperbarui
dalam commit rilis yang sama.

## 7. Dependensi

Pin exact di lock (ADR-02); base image pin digest; bump = baca release
notes + tests hijau + catat CHANGELOG. Lock runtime/dev dipisah;
image prod hanya lock runtime.

## 8. Fork

Fork mewarisi kebijakan ini. File inti yang diubah fork = tanggung jawab
fork saat merge (lihat `docs/FORK.md` checklist).
