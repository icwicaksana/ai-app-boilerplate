# Rencana v0.9.0 — Skills on-demand + Subagents

Status: **dieksekusi** (mulai 2026-11-16). Basis: v0.8.3 (`9421e80`).

Dokumen ini sengaja disimpan **di dalam repo** (bukan `Docs/` di luar git) supaya
setiap fork klien mewarisi rencana ini dan bisa menelusuri alasan keputusan.

---

## 0. Keputusan yang sudah dikunci

| # | Keputusan | Alasan |
|---|---|---|
| K1 | Roster subagent **statis di kode** | HITL & tool set tidak boleh ditentukan LLM; audit harus bisa dienumerasi |
| K2 | LLM hanya memutuskan **kapan, ke siapa, dan Brief** | Budget boleh diturunkan LLM, tidak boleh dinaikkan |
| K3 | **Tidak ada event SSE baru** | `schemas/stream.py` beku sejak v0.1; pakai `tool_start`/`tool_end` bernama `subagent__<nama>` (garis bawah ganda — titik dua lolos `@tool` tetapi ditolak API OpenAI `^[a-zA-Z0-9_-]+$`, dan gateway kita OpenAI-compatible; diverifikasi probe F4) |
| K4 | `durability="sync"` **hanya jalur chat streaming** | Di situlah race interrupt terbukti; jalur lain tidak diubah |
| K5 | Indeks skill ditaruh **di akhir** system prompt | Bagian statis di depan tetap stabil untuk prefix-cache |
| K6 | Router skill (RRF/embedding/Jev) **ditunda** | <10 skill; ambang keputusan ada di §7 |
| K7 | Rekursi subagent **ditolak** | Tidak ada kebutuhan terbukti; audit & biaya sulit diprediksi |
| K8 | Skill `scripts/` tidak dieksekusi | Butuh sandbox executor → v1.0 |
| K9 | **Filter stream berbasis TAG, bukan kedalaman** | Diukur: di dalam `create_agent`, model graph ROOT berjalan di namespace `model:<uuid>` (depth 1) — SAMA dengan child di `tools:<uuid>`. Kedalaman tidak membedakan induk/anak. `parent_ids` (root=2, child=5) hanya heuristik internal LangGraph. Maka penandaan eksplisit wajib, dan `make_subagent_tool()` jadi satu-satunya jalur sah. |
| K10 | **Guard binding di FACTORY, bukan di agen** | D11 menetapkan degradasi tanpa retriever itu disengaja (RAG mati → chat tetap jalan). Karena itu agen tidak boleh melempar. Yang harus membedakan adalah **jalur wiring**: chat boleh degraded, resume-approval dan fork **tidak**. Percobaan menaruh raise di `BaseChatAgent` menggagalkan `test_chat_degradasi_tanpa_retriever` dan sudah di-revert. |
| K11 | **Detail error 500 digenerikkan ke klien** | Pesan operasional (4xx, 502/503) lewat apa adanya; 500 → "Terjadi kesalahan internal."; exception tak dikenal → nama tipe saja. Detail penuh di log (korelasi `request_id`). `public_error_detail()` di `errors.py` dipakai handler HTTP + SSE. |
| K12 | **Direktori konten skill terpisah dari kode** | `SKILLS_DIR` default `skills/` (akar repo), bukan `app/skills/`. Konten operator campur modul Python = satu direktori baru bisa jadi error boot strict. |

---

## 1. Peta titik sentuh

| File | Status |
|---|---|
| `app/core/stream_policy.py` | baru |
| `app/core/untrusted.py` | baru |
| `app/agents/factory.py` | baru |
| `app/skills/{model,parser,validate,index,hash,loader,registry}.py` | baru |
| `app/agents/tools/SkillTools.py` | baru |
| `app/agents/SubagentBase.py` | baru |
| `app/agents/tools/DelegationTool.py` | baru |
| `app/agents/subagents/` | baru |
| `app/schemas/subagent.py` | baru |
| `app/scripts/{new_skill,new_subagent,skills_lint}.py` | baru |
| `app/controllers/runner.py` | ubah |
| `app/controllers/ChatController.py` | ubah |
| `app/core/approvals.py` | ubah |
| `app/controllers/ThreadController.py` | ubah |
| `app/agents/AgentContext.py` | ubah |
| `app/agents/BaseChatAgent.py` | ubah |
| `app/agents/utils/MiddlewareBuilderUtils.py` | ubah |
| `app/core/config.py` | ubah |
| `app/main.py` | ubah |
| `app/api/v1/routes/health.py` | ubah |
| `app/schemas/stream.py` | **TIDAK disentuh** |

---

## 2. F0 — Gate & fondasi (blocking)

| # | Item | Bukti penutup |
|---|---|---|
| F0-1 | `StreamPolicy` di `app/core/stream_policy.py` | unit: event bersarang ditolak |
| F0-2 | Integrasi di `Runner.stream` | regresi: 0 token subagent |
| F0-3 | `name=self.AGENT_NAME` di `create_agent` | event punya `name` |
| F0-4 | `disable_streaming=True` untuk LLM subagent | `on_chat_model_stream` = 0 |
| F0-5 | `app/agents/factory.py` dipakai 3 jalur | `bound_tools` identik di ketiganya |
| F0-6 | **Factory dengan mode eksplisit** (`allow_degraded`) | jalur resume/fork tidak boleh degraded → error, bukan `bound_tools=[]` |
| F0-7 | `durability="sync"` jalur chat | interrupt tidak hilang |

**Gate:** seluruh unit hijau + tes regresi kebocoran lulus + `bound_tools` konsisten.

---

## 3. F1 — Skill core (murni, tanpa I/O)

Kontrak frontmatter (dikunci di sini; field tambahan nanti = migrasi konten semua fork):

```yaml
name: sop-garansi
description: Prosedur klaim garansi 12 bulan, syarat, SLA 3 hari
when_to_use: user menanyakan garansi, klaim rusak, atau masa berlaku
tags: [garansi, klaim, purna-jual]
```

| Modul | Isi |
|---|---|
| `model.py` | `Skill` dataclass |
| `parser.py` | Parse frontmatter + body; tolak YAML rusak |
| `validate.py` | E01–E08 |
| `index.py` | Indeks L1 byte-stabil, alfabetis |
| `hash.py` | `content_hash` + `manifest_id` |

**Gate:** `index()` deterministik (2× panggil identik).

---

## 4. F2 — Skill runtime

Settings grup v0.9 di `config.py`:

```python
SKILLS_ENABLED: bool = False
SKILLS_DIR: str = "skills"  # direktori konten, BUKAN app/skills (K12)
SKILLS_STRICT: bool = True  # skill rusak gagalkan boot (pengecualian: dir hilang)
SKILLS_INDEX_INCLUDE_WHEN: bool = True
SKILLS_MAX_ENTRIES: int = 40
SKILL_BODY_MAX_CHARS: int = 20_000
SUBAGENTS_ENABLED: bool = False
SUBAGENT_MAX_TOKENS: int = 8_000
SUBAGENT_TIMEOUT_S: int = 45
SUBAGENT_MAX_DEPTH: int = 1
```

**Gate:** `SKILLS_ENABLED=false` → system prompt **byte-identik** v0.8.3 (tes snapshot).

---

## 5. F3 — Skill tools + integrasi

- `make_load_skill_tool(registry)` / `make_read_skill_file_tool(registry)` — factory-bound
- Indeks di akhir system prompt (K5)
- `load_skill`/`read_skill_file` masuk `RETRIEVAL_TOOL_NAMES` + `exclude_tools` (menutup blocker S1/S2)
- `core/untrusted.py` — guard injeksi bersama (memori, RAG, skill)

**Gate:** body skill bertahan setelah 12 giliran (simulasi `ClearToolUsesEdit`).

---

## 6. F4 — Subagent

```python
Report = {
  "status": "ok" | "partial" | "failed" | "timeout",
  "answer": str,
  "citations": [...],
  "failures": [{"tool": ..., "class": ..., "attempts": ...}],
  "tokens_used": int, "ms": int,
}
```

- `make_subagent_tool()` **wajib** — memasang middleware + LLM + tag
- Retry di level **tool** (idempotency key), bukan mengulang subagent
- Circuit breaker: 3 kegagalan/60s → buka 5 menit
- Tiga timeout berlapis: tool 10s / subagent 45s / request 60s
- Subagent **tidak boleh** memegang tool tulis ber-approval

Implementasi (2026-11-17):
- `SubagentBase` (kelas) DIGANTI `SubagentSpec` (data beku): tidak ada
  perilaku yang perlu di-override subclass — semua perilaku hidup di
  `make_subagent_tool`. Roster = `SUBAGENTS` dict + `register()`.
- Nama tool `subagent__<nama>` (K3 revisi — titik dua ditolak API OpenAI).
- Guard build (SubagentConfigError, LOUD): spec tak dikenal, tool hilang,
  tool approval-gated (sumber: `HITL_POLICY`), tool delegasi (rekursi).
- Retry: wrapper per-tool 3× backoff+jitter + timeout 10s/attempt,
  breaker proses-lifetime per (spec, tool); loop agen = retry lapis 2.
- Budget: `min(spec, settings, brief)` — brief hanya boleh menurunkan;
  over-budget → partial + entri failures.
- Mount di `build()` setelah blok skill (guard ganda flag+roster);
  misconfig melempar saat build, bukan skip diam-diam.

**Gate:** subagent gagal → `status="failed"`, agen utama tidak mengarang.

---

## 7. Ambang untuk membuka kembali topik router skill

| Pemicu | Ambang | Aksi |
|---|---|---|
| Jumlah skill | > 25 entri | Evaluasi RRF |
| Ukuran indeks | > 1.000 tok | Evaluasi RRF |
| Salah-pilih | > 10% | Evaluasi RRF |
| Recall | < 0,90 pada 30 tugas | Evaluasi RRF |
| Skill > 100 **dan** butuh confidence terkalibrasi | — | Pertimbangkan Jev |

Telemetri `skill_load` (F3) adalah sumber data untuk pemicu di atas.

---

## 8. F5–F7

- **F5:** `new_skill`, `new_subagent`, `skills_lint`. Gate: `delta fork ≈ 0` (0 edit file inti)
- **F6:** eval 30 tugas A/B — `savings_ratio ≥ 0,30`, recall ≥ 0,90, presisi ≥ 0,80, 0 load di 18 tugas adversarial. Docs: `docs/SKILLS-V09.md`, `UPGRADE.md`, `FORK.md`
- **F7:** versi `v0.9.0`, CHANGELOG, tag, push

---

## 9. Risiko utama

| Risiko | Dampak | Mitigasi |
|---|---|---|
| F3 menyentuh `build()` semua agen | Regresi menyeluruh | Snapshot prompt; `SKILLS_ENABLED=false` byte-identik |
| Middleware menghapus body skill | Fitur jalan tapi tak berguna | Tes simulasi `ClearToolUsesEdit` |
| Subagent melewati HITL | **Keamanan** | `make_subagent_tool()` wajib + tes larangan tool tulis |
| Fork mengedit file inti | `delta fork` membengkak | Scaffold + `skills_lint` |

---

## 10. Di luar cakupan

Router skill (RRF/embedding/Jev) · eksekusi `scripts/` · upload skill dari user ·
hot-reload skill · Text-to-SQL · spawn dinamis (v0.10+, capability-scoped).

Temuan review QA F2 yang SENGAJA ditunda (pre-existing, bukan regresi v0.9 —
jangan kerjakan diam-diam di tengah F3/F4):
- approvals membangun graph penuh (butuh LLM) hanya untuk baca checkpoint.
- Fork membuang interrupts + `deepcopy` values tanpa batas ukuran.
- Nama tool `subagent:<name>` (K3) harus diverifikasi lawan validator nama
  tool LangChain SEBELUM F4 menulis `make_subagent_tool` — titik dua sering
  ditolak (`^[a-zA-Z0-9_-]+$`); bila ditolak, K3 direvisi (mis. `subagent__x`).
- P0-3 versi reviewer ("klaim sebelum validasi agen") adalah false positive:
  `_assert_not_stale` (sebelum klaim) sudah memanggil `_graph_for` yang
  melempar `AgentNotFoundError` untuk agen tak dikenal.
