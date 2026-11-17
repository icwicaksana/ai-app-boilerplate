# UPGRADE — naik ke v0.9.0

v0.9.0 **kompatibel penuh** bila kedua flag mati (default): system prompt
byte-identik dengan v0.8.3 (dikunci `tests/test_skills_disabled_identical.py`),
tidak ada migrasi DB, tidak ada event SSE baru.

## 1. Upgrade tanpa mengaktifkan fitur (aman, disarankan dulu)

1. Tarik tag `v0.9.0`, install ulang dependensi (`PyYAML` baru).
2. Deploy seperti biasa. Perilaku identik v0.8.3.
3. Verifikasi: `/health` → `checks.skills: "disabled (SKILLS_ENABLED=false)"`.

Rollback: deploy ulang tag lama. Tidak ada state yang perlu dibersihkan
(registry skill/subagent murni in-memory per proses).

## 2. Mengaktifkan skills

1. Tulis skill pertama: `python -m scripts.new_skill sop-utama`, isi,
   `python -m scripts.skills_lint` sampai BERSIH.
2. Set `SKILLS_ENABLED=true` (tetap `SKILLS_STRICT=true`).
3. Deploy; cek log `skills siap: N skill, manifest=…` dan
   `/health` → `checks.skills: "ok (…)"`.
4. Uji via chat: tanya sesuatu yang jawabannya HANYA ada di skill, lalu
   tanya basa-basi. Basa-basi tidak boleh memicu `load_skill` (cek log
   `skill_load`).

## 3. Mengaktifkan subagents

1. Set `SUBAGENTS_ENABLED=true`. Roster template berisi 1 demo
   (`demo-stok`, tool `check_stock` — deterministik, tanpa efek nyata).
2. Deploy; uji: "cek stok ITEM-99" → boleh dijawab langsung ATAU via
   `subagent__demo-stok` (keduanya benar untuk demo sekecil ini).
3. Tulis subagent sungguhan pertama via `python -m scripts.new_subagent`,
   daftarkan 1 import, lint, deploy.

## 4. Jebakan umum

| Gejala | Penyebab | Obat |
|---|---|---|
| Boot gagal `SkillStartupError` | skill rusak + strict | baca daftar error, perbaiki, atau `SKILLS_STRICT=false` sementara (tidak disarankan permanen) |
| Skill tak pernah dimuat | `description`/`when_to_use` kabur | tulis dengan kata yang dipakai user; uji dengan eval |
| `subagent__*` tak muncul di tools | flag mati / roster kosong | cek `SUBAGENTS_ENABLED` + import spec terdaftar |
| Chat 500 `SubagentConfigError` | spec menyebut tool tak ada / tool approval | perbaiki spec — ini LOUD by design, jangan di-skip |
| Token membengkak setelah aktif | delegasi untuk tugas sepele | instruksikan di prompt induk: delegasikan bila >3 tool call; pantau log `subagent_end` |

## 5. Batas yang perlu diketahui

- Tanpa hot-reload: ubah skill/spec → deploy baru.
- Tanpa router: >25 skill → baca ambang di `docs/V09-PLAN.md` §7.
- `scripts/` di skill tidak dieksekusi (butuh sandbox → v1.0).
- Timeout delegasi 45 dtk: tugas lebih lama harus dipecah di sisi induk.
