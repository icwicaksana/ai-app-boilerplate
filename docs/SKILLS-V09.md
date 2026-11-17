# Skills on-demand + Subagents (v0.9)

Panduan operator dan fork. Desain lengkap: `docs/V09-PLAN.md`.

## 1. Konsep 5 menit

**Masalah.** Setiap prosedur (SOP garansi, kebijakan retur, …) yang ditulis
di system prompt dibayar tokennya di SETIAP giliran — bahkan saat user hanya
bilang "halo". 10 prosedur × 1.000 token = 10 ribu token per giliran.

**Solusi.** Prosedur disimpan sebagai **skill** (file Markdown). System prompt
hanya membawa **indeks** 1 baris per skill (~100 token). Agen memuat skill
dengan tool `load_skill` HANYA saat tugas membutuhkannya.

**Subagent** memisahkan tugas riset berat ke pekerja berkonteks sendiri:
pekerja menerima Brief ringkas (bukan seluruh riwayat) dan mengembalikan
Report ringkas. Riwayat induk tidak tercemar 20 tool-call riset.

```
tanpa skill          dengan skill (6 skill, 1 dimuat)
prompt: 6.000 char   prompt: 900 char indeks + 1.000 body = 1.900 char
setiap giliran       hemat ~68% per giliran
```

## 2. Menulis skill

```bash
python -m scripts.new_skill sop-garansi
python -m scripts.skills_lint
```

Hasil: `skills/sop-garansi/SKILL.md`:

```markdown
---
name: sop-garansi
description: Prosedur klaim garansi, syarat berkas, dan SLA penanganan
when_to_use: user menanyakan garansi, klaim rusak, atau masa berlaku
tags: [garansi, klaim]
---

# Prosedur Klaim Garansi
1. ...
```

**Aturan menulis** (yang dilanggar = skill tidak dipakai atau ditolak boot):

| Aturan | Kenapa |
|---|---|
| `description` 1 baris, tanpa angka/fakta jawab | Satu-satunya yang dilihat model di indeks; kalau sudah berisi jawaban, model tak pernah memuat (recall eval jatuh) |
| `when_to_use` konkret ("user tanya X") | Penyebab tersering "skill tidak dipakai" adalah pemicu yang kabur |
| `name` == nama direktori | Satu sumber kebenaran; E08 menolak yang beda |
| Body ≤ 20.000 char; detail ke `references/` | Body dibayar per pemuatan; L3 dibaca terpisah via `read_skill_file` |
| Jangan tulis instruksi anti-sistem | Body adalah DATA, bukan instruksi — tetap di-quoting saat dimuat |

**Siklus hidup:** ubah skill → `skills_lint` → deploy baru → cek log
(`manifest=…`) dan `/health` → `checks.skills`. Tidak ada hot-reload
(perubahan = deploy; alasannya di `app/skills/registry.py`).

## 3. Menambah subagent

```bash
python -m scripts.new_subagent cek-pesanan
```

Hasil: `app/agents/subagents/cek_pesanan.py` berisi `SubagentSpec` +
`register()`. Daftarkan dengan 1 baris import di composition root fork-mu,
lalu `python -m scripts.skills_lint`.

**Batasan yang dipaksakan kode** (bukan imbauan):

- Roster **statis di kode** — LLM memilih kapan/siapa/Brief, tidak pernah
  tool set, middleware, atau menaikkan budget.
- Tool approval-gated (`HITL_POLICY`) **dilarang** — build melempar
  `SubagentConfigError`. Interrupt bersarang tak sampai ke checkpoint induk.
- Rekursi **ditolak** — spec yang menyebut tool `subagent__*` melempar.
- Spec salah konfigurasi melempar **saat build**, bukan skip diam-diam.

**Kontrak Report:** `ok` tak boleh bawa failures; `partial`/`failed` wajib
menyebut yang gagal; `timeout` saat melewati batas. Agen utama wajib
menyampaikan apa yang TIDAK didapat — jangan jawab seolah data lengkap.

## 4. Konfigurasi

```bash
SKILLS_ENABLED=true          # default false (mati = perilaku v0.8.3 identik)
SKILLS_DIR=skills
SKILLS_STRICT=true           # skill rusak gagalkan boot (disarankan)
SKILLS_MAX_ENTRIES=40
SKILL_BODY_MAX_CHARS=20000
SUBAGENTS_ENABLED=true
SUBAGENT_MAX_TOKENS=8000
SUBAGENT_TIMEOUT_S=45
SUBAGENT_MAX_DEPTH=1         # rekursi ditolak; jangan naikkan tanpa desain
```

## 5. Operasi

- **Health:** `/health` → `checks.skills`: `ok (N skill, manifest=…)` |
  `degraded (…)` | `disabled (…)` | `not initialized`. Skills tidak pernah
  membuat `/ready` 503 (fail-open seperti RAG).
- **Boot strict:** `SKILLS_STRICT=true` (default) — 1 skill rusak = boot
  gagal dengan daftar semua yang rusak. Pengecualian yang disengaja:
  direktori hilang bukan fatal (deployment boleh menyala dulu).
- **Telemetri:** log `skill_load name=… chars=…` dan `subagent_end …`
  adalah sumber data ambang router (plan §7): >25 skill, indeks >1.000
  token, salah-pilih >10%, atau recall <0,90 → evaluasi RRF.
- **Biaya subagent:** setiap delegasi = 1 run model extra. Aturan praktis:
  delegasikan bila tugas butuh >3 tool call mandiri, atau konteks kerja
  >2.000 token yang tak perlu dilihat induk.

## 6. Evaluasi (diulang tiap rilis minor)

```bash
python -m pytest tests/integration/test_eval_skills_live.py -s
```

Butuh gateway LiteLLM hidup; bila mati, tes SKIP (retrieval tetap diuji
di eval lain). Gate: recall ≥ 0,90 · presisi ≥ 0,80 · 0 load di 18 tugas
adversarial · savings ≥ 0,30 · keyword ≥ 0,70. Hasil rilis v0.9.0 ada di
commit F6 dan CHANGELOG.
