# FORK — panduan fork klien (v0.9)

Prinsip: fork yang sehat **menambah file, bukan mengedit file inti**.
`delta fork` = baris yang diubah di file template. Target: `≈ 0`.

## 1. Yang BOLEH disentuh (zona fork)

| Aset | Cara |
|---|---|
| `skills/<nama>/SKILL.md` (+ `references/`) | `python -m scripts.new_skill`, tanpa edit kode |
| `app/agents/subagents/<fork>*.py` | `python -m scripts.new_subagent`, 1 file per subagent |
| 1 baris import spec | di composition root fork-mu (modul milik fork) |
| `.env` | flag `SKILLS_*` / `SUBAGENT_*`, model, dsb |
| Agen chat fork | copy `TemplateChatAgent.py` → modul baru (jangan edit template) |
| Tool fork | 1 file per tool di `app/agents/tools/` + daftar di ToolRegistry bila global |

## 2. Yang JANGAN disentuh (zona inti)

`app/skills/*` · `app/agents/BaseChatAgent.py` · `app/agents/factory.py` ·
`app/agents/tools/{SkillTools,DelegationTool}.py` · `app/core/{stream_policy,untrusted,errors}.py` ·
`app/agents/utils/MiddlewareBuilderUtils.py` · `app/schemas/*` ·
`app/controllers/*` · `tests/test_skills_disabled_identical.py`.

Butuh perilaku inti yang berbeda? Ajukan ke upstream template dulu —
mengedit inti membuat merge rilis berikut mahal.

## 3. Checklist sebelum merge rilis baru

1. `python -m scripts.skills_lint` → BERSIH.
2. Suite unit hijau (`pytest tests/ -m "not integration"`).
3. Gate byte-identik hijau (otomatis di suite — jangan ubah hash-nya; bila
   merah setelah merge, upstream mengubah prompt: baca CHANGELOG).
4. Eval live (`tests/integration/test_eval_skills_live.py`) + eval fork-mu
   sendiri bila menambah skill: recall/presisi tidak turun.
5. `git diff --stat` terhadap tag upstream: file inti harus 0 baris berubah.

## 4. Menambah skill tanpa menambah risiko

- Patuhi kontrak frontmatter (`name/description/when_to_use/tags`) —
  menambah field sendiri = migrasi di semua fork.
- Jaga `description` bebas fakta jawab (rinci di `docs/SKILLS-V09.md` §2).
- Tulis 2 tugas eval per skill baru (1 positif wajar + 1 parafrasa) ke dalam
  eval fork-mu; target recall fork ≥ 0,90 seperti template.

## 5. Menambah subagent tanpa menambah risiko

- Tool set minimal: hanya tool yang benar-benar dibutuhkan pekerja.
- Jangan pernah daftarkan tool approval-gated atau tool `subagent__*` —
  build menolak, dan lint menangkap lebih awal.
- Budget: mulai dari default (8.000 token / 45 dtk / 10 call), turunkan
  setelah melihat log `subagent_end` produksi.
- Beri `when_to_use` yang membedakan dari skill: subagent untuk tugas
  multi-langkah mandiri, skill untuk prosedur yang dibaca induk.
