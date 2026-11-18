# AGENTS.md — Panduan Agen ai-app-boilerplate

> **Status:** SAH untuk `v2.0.0` (commit `ccf7de4`, 2026-11-18).
> **Audiens:** agen AI yang bekerja di repo ini atau fork-nya — manusia boleh baca, tapi ditulis untuk mesin.
> **Cara baca:** tiap klaim perilaku menunjuk `file:baris` relatif ke akar repo.
> Pointer = kebenaran; dokumen ini TIDAK menyalin logika kode. Klaim bertanda
> `[BELUM VERIFIKASI]` jangan dijadikan acuan (target: nol penanda).
> **Aturan rawat:** ubah kode → ubah dokumen ini di PR/commit yang sama.
> Jangan tambah klaim tanpa pointer. Verifikasi pointer basi tiap rilis minor.

## Daftar isi

- §1 Peta repo (HEAD, direktori, entry point, alur request)
- §2 Aturan besi / invarian
- §3 How-to (agen, tool, subagent, skill, event)
- §4 Kontrak API (konvensi, auth, SSE, WS, jobs, threads, HITL, health, dokumen)
- §5 Subsistem RAG (retrieval, normalisasi, ingest, graph, eval, config)
- §6 Ops (setup, env, docker, jalan, migrasi, runbooks, CI, Windows)
- §7 Testing (unit vs integrasi, fakes, gate, canary, tambah tes)
- §8 Fork-per-klien (clone, zona, upgrade, TestFE)

---

## §1 Peta repo

### 1.1 HEAD v2.0.0

- HEAD: commit `ccf7de4` — "release v2.0.0: event text, trace eksekusi,
  narasi satu-suara"; `git describe` = `v2.0.0`; branch `main` bersih.
- Tema rilis (dari pesan commit + kode): event SSE `text`, `trace`
  terstruktur di `result` (`app/schemas/stream.py:74`, `app/controllers/runner.py:232`),
  dan narasi satu-suara always-on (`app/agents/BaseChatAgent.py:69`).
- Repo adalah boilerplate FastAPI + LangGraph: dua rel AI App —
  chat streaming (SSE/WS) dan pipeline sync (jobs) —
  (`app/controllers/registry.py:1`, `app/api/v1/router.py:16`).

### 1.2 Struktur direktori `app/*`

| Direktori | Peran (satu baris) | Berkas kunci |
|---|---|---|
| `app/api/v1/routes/` | Route tipis: auth, chat SSE, WS, jobs, threads, approvals, documents, health | `router.py:16` (agregat), `routes/chat.py:65`, `routes/jobs.py:45`, `routes/ws.py:1` |
| `app/controllers/` | Rel bisnis: susun thread/config, rakit agen, panggil agen | `base.py:11`, `ChatController.py:33`, `JobController.py:29`, `runner.py:105`, `registry.py:24` |
| `app/agents/` | Agen chat (ReAct) + pipeline (StateGraph) + factory context | `BaseChatAgent.py:86`, `BasePipelineAgent.py:27`, `factory.py:53`, `AgentContext.py:22` |
| `app/agents/tools/` | Tool stateless (registry) + tool factory-bound | `ToolRegistry.py:7`, `ExampleSearchTool.py:8`, `MemoryTool.py:1`, `SkillTools.py:1`, `DelegationTool.py:1` |
| `app/agents/subagents/` | Roster statis + tool delegasi | `registry.py:20`, `demo.py:16`, `__init__.py:13` |
| `app/agents/state/` | State pipeline kaku (chat ReAct TIDAK pakai ini) | `BaseState.py:1` |
| `app/agents/middleware/` | Middleware HITL | `MiddlewareBuilderUtils.py:99` (pemasangan), `app/core/hitl.py:1` (policy) |
| `app/core/` | Identitas, error, policy stream, turn, approvals, config, trace run | `auth.py:174`, `threads.py:1`, `errors.py:1`, `stream_policy.py:71`, `turns.py:33`, `config.py:54`, `run_trace.py:1` |
| `app/schemas/` | Kontrak wire (model Pydantic saja) | `chat.py:10`, `stream.py:1`, `job.py:12-41` (`JobRequest{thread_id?,payload,metadata}`, `JobResponse{thread_id,status,output,interrupt?,meta}`) |
| `app/helpers/` | Perakit respons (satu-satunya builder) | `response.py:1` |
| `app/infra/` | Pool Postgres, cache, LLM, turn log, RAG clients | `lifespan.py`, `postgres.py`, `cache.py`, `llm.py`, `turnlog.py:29`, `rag/factory.py` |
| `app/skills/` | Registry skill boot-only (tanpa hot-reload) | `registry.py:22`, `boot.py:34`, `loader.py:60`, `parser.py:25`, `validate.py:1` |
| `app/rag/` | Domain RAG murni: pipeline, ports, rank, grounding, citations, normalize, chunker, kg_seed, verbalize, ontology, parsers/{sniff,markdown} (detail §5) | `pipeline.py`, `ports.py` |
| `app/workers/` | Proses terpisah ingest/reindex RAG | `ingest.py` (tanpa argumen CLI, `:805-808`), `reindex.py:252` (`--workspace/--since/--dry-run`) |
| `app/scripts/` | Utilitas sekali jalan (buat user) | `create_user.py` |
| `skills/`, `scripts/` | Direktori skill aktif + scaffold (`new_skill.py`, `new_subagent.py`) | `skills/_template/SKILL.md:1`, `scripts/new_skill.py:1`, `scripts/new_subagent.py:1` |

### 1.3 Entry point

- **API (kanonis):** `python -m app.main` (`app/main.py:348`).
  Memilih loop selector di Windows (`app/main.py:351`), lalu serve
  `app.main:app` via uvicorn (`app/main.py:357`). Tanpa entrypoint ini,
  lifespan menolak start bila loop Proactor terdeteksi (`app/main.py:92`).
- **`create_app()`** (`app/main.py:278`): rakit FastAPI + middleware
  (CORS, RequestID, SecurityHeaders, Metrics) (`app/main.py:294`),
  handler error RFC 7807 (`app/main.py:308`), dan mount `v1_router`
  di `API_V1_PREFIX` (`app/main.py:312`). Endpoint `/metrics` ter-guard
  token/loopback (`app/main.py:314`).
- **`lifespan()`** (`app/main.py:83`): urutan start — pool Postgres
  (`app/main.py:101`), checkpointer+store (`app/main.py:112`),
  skema auth (`app/main.py:124`), skema HITL (`app/main.py:132`),
  migrasi RAG 007 (`app/main.py:145`), cache Valkey (`app/main.py:168`),
  turn log (`app/main.py:184`), klien RAG (`app/main.py:192`), registry
  skill (`app/main.py:208`). Gagal di tengah = tutup yang sudah dibuka
  lalu raise (`app/main.py:158`); shutdown menutup RAG → turn → cache →
  pool (`app/main.py:256`).
- **Setup repo:** `setup.ps1` — buat `.venv` (`setup.ps1:19`), install
  `requirements.txt` (`setup.ps1:30`), salin `.env` (`setup.ps1:37`),
  generate `JWT_SECRET` (`setup.ps1:44`) dan `SOURCE_CONN_KEY`
  (`setup.ps1:54`), lalu pytest non-integrasi (`setup.ps1:66`).
  Layanan wajib: `app-db` + `cache` via docker compose; worker RAG
  jalan sebagai proses terpisah (`setup.ps1:69`).

### 1.4 Alur request route → controller → agent → runner → graph

- **Chat SSE (`POST /chat/stream`):** route preflight PENUH sebelum turn
  ada (`app/api/v1/routes/chat.py:78`) → `ChatController.preflight`
  resolve thread + build agen + guard approval 409
  (`app/controllers/ChatController.py:88`) → `TurnManager.start` dengan
  generator `ctrl.stream` (`app/api/v1/routes/chat.py:85`) → klien tail
  via `turns.follow` (`app/api/v1/routes/chat.py:105`). Tanpa cache:
  streaming langsung tanpa resume (`app/api/v1/routes/chat.py:81`).
- **Resume (`GET /chat/stream/{turn_id}`):** cek ada + pemilik, lalu
  replay dari `Last-Event-ID` (`app/api/v1/routes/chat.py:124`).
- **Controller chat:** `stream()` validasi ulang (400/403/404 jadi event
  error, bukan raise) (`app/controllers/ChatController.py:104`), rakit
  agen per eksekusi (`app/controllers/ChatController.py:45`), delegasi
  ke `agent.stream_agent` (`app/controllers/ChatController.py:151`).
- **Agen chat:** `stream_agent`: preprocess → load memori paralel →
  `_to_state` → `runner.stream` → `stream_success`
  (`app/agents/BaseChatAgent.py:306`). Graph dibangun 1x saat init
  (`app/agents/BaseChatAgent.py:109`).
- **Runner → graph:** `Runner.stream` yield `started`, pompa
  `astream_events v2`, saring via policy, baca final dari checkpoint
  (`app/controllers/runner.py:153`); `Runner.run` = `ainvoke` sekali
  (`app/controllers/runner.py:128`); `resume` = `Command(resume=...)`
  (`app/controllers/runner.py:387`).
- **Jobs sync (`POST /jobs/{agent}`):** rate-limit → cap payload →
  idempotency guard (`app/api/v1/routes/jobs.py:55`) → `JobController.run`
  → `agent.run_agent` → `runner.run`
  (`app/controllers/JobController.py:70`, `app/agents/BasePipelineAgent.py:79`).
- **WS (`/ws/chat`):** ticket sekali pakai → handshake → ack + pompa
  event envelope + `seq` (`_pump_turn` di `app/api/v1/routes/ws.py:336`,
  `turns.follow` di `:338`, drop droppable `:431`, `seq` `:464`);
  `cancel`/`ping`/`resume` didukung (`app/api/v1/routes/ws.py:1`).

## §2 Aturan besi / invarian

### 2.1 Identitas dari JWT

- Satu pintu: `resolve_identity()` dipakai SEMUA route via
  `get_thread_identity` (`app/core/auth.py:174`, `app/core/dependencies.py:100`).
- Trust-header DIHAPUS total di v0.8.1 — header bisa dipalsukan
  (`app/core/auth.py:178`).
- Token invalid/expired/tipe salah → 401; klaim non-kanonis ditolak
  (`app/core/auth.py:156`, `app/core/auth.py:138`). Tanpa `JWT_SECRET` →
  503 `AUTH_NOT_CONFIGURED` (`app/core/auth.py:159`); prod mewajibkan
  secret min 32 char (`app/core/config.py:309`, `app/core/config.py:336`).
- Scope KETAT: token tanpa scope tak bisa masuk area ber-gate; chat/jobs
  di-gate per-router (`app/core/auth.py:185`, `app/api/v1/routes/chat.py:40`,
  `app/api/v1/routes/jobs.py:39`).
- `thread_id` BEKU `workspace:user:session`, satu-satunya parser di
  `threads.py`; format lama ditolak 400, milik orang lain 403
  (`app/core/threads.py:1`, `app/core/threads.py:56`, `app/core/threads.py:73`).

### 2.2 Runner satu-satunya pintu graph

- Agen DILARANG `Runner()` sendiri: controller bikin, inject via
  `AgentContext` (`app/controllers/runner.py:1`, `app/agents/AgentContext.py:22`,
  `app/agents/BaseChatAgent.py:93`, `app/agents/BasePipelineAgent.py:34`).
- Kontrak `stream`: `started` pertama, TEPAT SATU `result`|`error`
  terminal (`app/controllers/runner.py:9`). Final dibaca dari checkpoint,
  TANPA eksekusi ulang (`app/controllers/runner.py:358`).
- Jalur chat streaming WAJIB durability `sync` (baca-tepat-setelah-tulis);
  job/resume pakai default (`app/core/dependencies.py:36`).
- Interrupt dibaca dari `snapshot.interrupts`, BUKAN `values`
  (`app/controllers/runner.py:358`); dinormalisasi ke `{id, value,
  response_schema}` (`app/controllers/runner.py:52`).

### 2.3 Kontrak beku

- **Thread:** format `workspace:user:session` BEKU permanen
  (`app/core/threads.py:1`); request boleh kosong → server susun dari
  klaim JWT (`app/schemas/chat.py:12`, `app/controllers/base.py:35`).
- **SSE:** `started → text*/thinking*/tool_* → result|error`; tanpa baris
  `[DONE]`; tiap event dibungkus envelope transport
  (`app/schemas/stream.py:3`, `app/schemas/stream.py:7`). Model di
  `schemas/stream.py`, perakitan HANYA di `helpers/response.py`
  (`app/schemas/stream.py:1`, `app/helpers/response.py:1`).
- **Urutan error job BEKU:** 400/403 (thread) SEBELUM 404 (agen)
  (`app/controllers/JobController.py:80`).
- **`trace` di `result`:** aditif, kontrak di `docs/TRACE-CONTRACT.md`
  (terverifikasi ada; ringkasan akurat di §4.3.4 dokumen ini)
  (`app/schemas/stream.py:74`).
- Error sync = exception + handler (status HTTP benar); JANGAN 200 berisi
  error (`app/helpers/response.py:9`, `app/core/errors.py:1`).

### 2.4 Fail-closed & tanpa fallback diam

- **Fail-closed (contoh terkunci):** `StreamPolicy.accept` tolak event tak
  dikenal / tag aneh / exception (`app/core/stream_policy.py:74`);
  resume salah-pemilik = 410 sama seperti hilang (`app/api/v1/routes/chat.py:144`);
  refresh token saat cache mati = 503 (`app/core/auth.py:91`); argumen
  tool tak terbaca = butuh approval (`app/core/hitl.py:42`); skill rusak
  + `SKILLS_STRICT` = gagal boot (`app/skills/boot.py:61`).
- **Tanpa fallback diam:** binding hilang = LOUD — `AgentBindingError`
  untuk resume/fork tanpa store/retriever (`app/agents/factory.py:77`,
  `app/agents/factory.py:134`); spec subagent rusak = `SubagentConfigError`
  saat build (`app/agents/tools/DelegationTool.py:52`); template dipakai
  langsung = `TypeError` (`app/agents/TemplateChatAgent.py:44`); nama
  tool terikat dikunci test via `bound_tools` (`app/agents/BaseChatAgent.py:173`).
- **Degradasi EKSPLISIT (bukan diam):** cache mati di tengah → direct
  stream + alarm + header `X-Degraded` (`app/api/v1/routes/chat.py:93`);
  memori gagal load → lanjut tanpa memori + tombstone pesan basi
  (`app/agents/BaseChatAgent.py:247`); denylist tak terbaca → izinkan +
  alarm (`app/core/auth.py:62`); `rag_chat` tanpa retriever → warning
  degradasi chat biasa (`app/agents/RagChatAgent.py:61`).

### 2.5 TOOLS vs factory-bound

- `ToolRegistry.TOOLS` HANYA untuk tool stateless (`check_stock`,
  `process_refund`) (`app/agents/tools/ToolRegistry.py:7`).
- Tool ber-identitas/ber-state WAJIB factory-bound (closure per request),
  SENGAJA tidak di registry: memori (`app/agents/tools/MemoryTool.py:3`),
  retrieval workspace-bound (`app/agents/tools/RetrieverTool.py:1`),
  skill terikat registry boot (`app/agents/tools/SkillTools.py:1`).
- Pengikatan terjadi di `BaseChatAgent.build`: memori
  (`app/agents/BaseChatAgent.py:120`), skill (`app/agents/BaseChatAgent.py:134`),
  subagent (`app/agents/BaseChatAgent.py:159`); RAG di `__init__`
  sebelum `super().__init__` (`app/agents/RagChatAgent.py:51`).
- Retriever HANYA untuk agen `NEEDS_RETRIEVER`, terikat workspace+clearance
  dari thread tervalidasi (`app/agents/factory.py:121`); chat ber-RAG saat
  RAG mati → 503 jelas (`app/controllers/ChatController.py:54`).

### 2.6 StreamPolicy root-only

- Invarian: HANYA percakapan ROOT terlihat user; run bersarang (subagent,
  klasifikasi internal) tidak pernah (`app/core/stream_policy.py:3`).
- Tiga lapis: (1) LLM child `disable_streaming`, (2) tag `subagent`/
  `internal`/`no-stream`, (3) tes regresi (`app/core/stream_policy.py:18`).
- Daftar event klien SENGAJA sempit (`on_chat_model_stream`,
  `on_tool_start`, `on_tool_end`) (`app/core/stream_policy.py:56`).
- Runner saring SEBELUM petakan; tracking timing tool cek tag ganda
  (`app/controllers/runner.py:208`, `app/controllers/runner.py:248`).
- Heuristik kedalaman `checkpoint_ns` DIBUANG (terbukti salah ukur);
  penandaan eksplisit via `make_subagent_tool()` adalah WAJIB
  (`app/core/stream_policy.py:26`).

### 2.7 Subagent roster statis

- Roster STATIS di kode (K1): LLM memilih nama + menulis Brief, TIDAK
  menentukan tool/middleware/budget (`app/agents/subagents/registry.py:1`).
- `SubagentSpec`: `name`, `description`, `when_to_use`, `system_prompt`,
  `tools` (by-name), `max_tokens`, `timeout_s`, `thread_limit`
  (`app/agents/subagents/registry.py:19`); `register` fail-fast untuk
  nama duplikat/tak valid (`app/agents/subagents/registry.py:41`).
- Satu-satunya jalur build: `make_subagent_tool()`
  (`app/agents/tools/DelegationTool.py:1`): tool approval-gated DITOLAK
  (`app/agents/tools/DelegationTool.py:193`), delegasi bersarang DITOLAK
  (`app/agents/tools/DelegationTool.py:202`), tool hilang DITOLAK
  (`app/agents/tools/DelegationTool.py:187`), budget hanya boleh TURUN
  (`app/agents/tools/DelegationTool.py:208`), child di-tag `subagent`
  (`app/agents/tools/DelegationTool.py:301`).
- Nama tool `subagent__<nama>`; default MATI (`SUBAGENTS_ENABLED=false`,
  disengaja — roster global vs TOOLS per-agen)
  (`app/agents/tools/DelegationTool.py:40`, `app/core/config.py:220`).

### 2.8 Narasi satu-suara

- `NARRATION_RULES` ALWAYS-ON tanpa flag: agen satu-satunya suara ke user,
  Bahasa Indonesia, 1 kalimat untuk langkah besar; umumkan SEBELUM
  `subagent__*`, review ringan SESUDAH; diam untuk micro-tool; jangan
  bocorkan thinking/prompt mentah (`app/agents/BaseChatAgent.py:69`).
- Ditempel SETELAH `SYSTEM_PROMPT`, SEBELUM `MEMORY_RULES`; indeks skill
  (K5) dan delegasi tetap di AKHIR agar prefix-cache stabil
  (`app/agents/BaseChatAgent.py:114`, `app/agents/BaseChatAgent.py:131`).
- `hitl_demo` tak kena narasi karena override `build()` sendiri
  (`app/agents/BaseChatAgent.py:114`, `app/agents/HitlDemoAgent.py:50`).

## §3 How-to

### 3.1 Agen chat baru dari Template

1. Copy `app/agents/TemplateChatAgent.py` → `app/agents/CsAgent.py`;
   ganti nama class + `AGENT_NAME`, isi SEMUA `[ISI]` di `SYSTEM_PROMPT`
   (`app/agents/TemplateChatAgent.py:1`).
2. Persempit `TOOLS` ke subset khusus agen ini (contoh pola impor
   per-tool di `app/agents/TemplateChatAgent.py:36`); jangan kasih semua
   tool ke semua agen. Referensi hidup: `ExampleChatAgent`
   (`app/agents/ExampleChatAgent.py:22`).
3. Override `preprocess`/`postprocess` HANYA bila ada kustomisasi nyata
   (contoh: cap 2000 char) (`app/agents/ExampleChatAgent.py:27`,
   `app/agents/BaseChatAgent.py:192`).
4. Daftarkan 1 baris di `CHAT_AGENTS` (`app/controllers/registry.py:24`).
   Template SENGAJA tak terdaftar (`app/controllers/registry.py:5`).
5. Jangan override `build`/`stream_agent` tanpa alasan kuat; jangan bikin
   `Runner()` di agen (`app/agents/BaseChatAgent.py:1`,
   `app/agents/BaseChatAgent.py:93`).
6. Uji: prompt `[ISI]` tersisa atau nama `template_chat` = `TypeError`
   (`app/agents/TemplateChatAgent.py:44`); kirim `POST /chat/stream`
   dengan `{"agent": "<nama>"}` (`app/schemas/chat.py:10`).

### 3.2 Agen pipeline

1. Pilih titik mulai: copy `ExamplePipelineAgent.py` (hidup) atau
   `TemplatePipelineAgent.py` (kerangka abstract)
   (`app/agents/TemplatePipelineAgent.py:9`).
2. Definisikan State (wajib `messages` untuk jejak runner) lalu isi denah
   kaku di `build()`: node Python deterministik → … → node LLM terakhir
   (`app/agents/TemplatePipelineAgent.py:26`,
   `app/agents/TemplatePipelineAgent.py:39`, `app/agents/state/BaseState.py:16`).
3. Aturan node: validasi/hitung = Python murni TANPA LLM; hanya node
   terakhir boleh LLM (narasi) (`app/agents/BasePipelineAgent.py:1`).
   Angka presisi = `Decimal` di Python, bukan dari LLM
   (`app/agents/ExamplePipelineAgent.py:33`).
4. `run_agent` bawaan base sudah: `_to_state` → `runner.run` →
   ekstraksi `FINAL_KEY`/AI-terakhir → `{"status","output","interrupt"}`
   (`app/agents/BasePipelineAgent.py:79`). Output terstruktur tervalidasi
   ikut sebagai `output.data` bila `raw["structured"]` dict
   (`app/agents/BasePipelineAgent.py:95`).
5. Daftarkan di `JOB_AGENTS` (`app/controllers/registry.py:32`); jalankan
   via `POST /jobs/{agent}` (`app/api/v1/routes/jobs.py:45`). Pipeline
   SENGAJA tanpa store/memori (`app/controllers/JobController.py:5`).

### 3.3 Tool: registry vs factory

- **Tool stateless baru:** duplikat `ExampleSearchTool.py` (satu tool =
  satu file, dekorator `@tool`) (`app/agents/tools/ExampleSearchTool.py:1`),
  tambah 1 baris di `TOOLS` (`app/agents/tools/ToolRegistry.py:7`).
  Tool tulis berisiko: daftarkan policy di `HITL_POLICY` + aktifkan
  `HITL_ENABLED` (`app/core/hitl.py:50`,
  `app/agents/utils/MiddlewareBuilderUtils.py:99`).
- **Tool factory-bound baru:** tiru `make_*_tool(...)` yang me-return
  closure terikat 1 resource (store/retriever/registry)
  (`app/agents/tools/MemoryTool.py:1`, `app/agents/tools/SkillTools.py:60`,
  `app/agents/tools/RetrieverTool.py:31`); JANGAN masukkan ke `TOOLS`;
  ikat di `build()` agen (`app/agents/BaseChatAgent.py:109`).
- **Keputusan cepat:** butuh identitas user/workspace/registry boot? →
  factory. Murni fungsi input→output? → registry.
- Middleware otomatis: limit tool-call, context editing, ubah exception
  tool jadi pesan ramah (`app/agents/utils/MiddlewareBuilderUtils.py:1`).

### 3.4 Subagent: spec + register

1. Scaffold: `python -m scripts.new_subagent <nama>`
   (`scripts/new_subagent.py:1`) atau duplikat `subagents/demo.py`
   (`app/agents/subagents/demo.py:1`).
2. Tulis `SubagentSpec(name, description, when_to_use, system_prompt,
   tools, max_tokens, timeout_s, thread_limit)` lalu `register(...)`
   (`app/agents/subagents/registry.py:19`,
   `app/agents/subagents/registry.py:41`). Nama lolos `SPEC_NAME_RE`,
   `description`+`when_to_use` wajib isi.
3. Pastikan modul ter-import saat boot (pola: `subagents/__init__.py`
   import `demo`) (`app/agents/subagents/__init__.py:13`); fork tambah
   1 baris import di composition root-nya (`scripts/new_subagent.py:1`).
4. `tools` = NAMA tool induk yang sudah ada (by-name); tool approval-gated
   dan `subagent__*` PASTI ditolak (`app/agents/tools/DelegationTool.py:186`).
5. Aktifkan `SUBAGENTS_ENABLED=true` (`app/core/config.py:220`); batas
   global `SUBAGENT_MAX_TOKENS`/`SUBAGENT_TIMEOUT_S` hanya menurunkan
   (`app/core/config.py:221`, `app/agents/tools/DelegationTool.py:208`).
6. Kontrak I/O: LLM kirim `Brief(task, constraints)`; child jawab
   `Report(status, answer, failures)` sebagai teks
   (`app/agents/tools/DelegationTool.py:252`;
   `Brief{task,constraints,budget_tokens?,timeout_s?}` di
   `app/schemas/subagent.py:20-54`;
   `Report{status,answer,citations?,failures?,tokens_used,ms}` di
   `app/schemas/subagent.py:66-114`).

### 3.5 Skill: SKILL.md + registry

1. Scaffold: `python -m scripts.new_skill <nama>`
   (`scripts/new_skill.py:1`) atau salin `skills/_template/SKILL.md`
   (`skills/_template/SKILL.md:1`).
2. Struktur: direktori `skills/<nama>/SKILL.md` + frontmatter YAML
   (`name`, `description`, `when_to_use`, `tags`) + body prosedur
   (`app/skills/parser.py:25`, `skills/_template/SKILL.md:1`).
   Berkas pendukung (L3) = file lain di direktori yang sama
   (`app/skills/loader.py:116`).
3. Hormati E01–E08: nama lowercase/dash, deskripsi 1 baris 10–200 char,
   `when_to_use` konkret, body ≤ 20.000 char, tanpa field asing, nama ==
   nama folder (`app/skills/validate.py:1`, `app/skills/validate.py:47`).
4. Registry dibangun SEKALI saat boot, tanpa hot-reload; perubahan skill
   = deploy baru (`app/skills/registry.py:1`, `app/skills/boot.py:34`).
   Direktori `_`-prefixed dilewati (`app/skills/loader.py:41`).
5. Agen memakai on-demand: indeks L1 di system prompt → `load_skill`
   → `read_skill_file`; body = DATA ter-quoting, bukan instruksi
   (`app/agents/BaseChatAgent.py:131`, `app/agents/tools/SkillTools.py:1`).
6. Operasi: `SKILLS_ENABLED=false` = perilaku identik v0.8.3
   (`app/skills/boot.py:36`); direktori hilang = registry kosong +
   error (non-fatal), skill rusak + strict = gagal boot
   (`app/skills/boot.py:40`, `app/skills/boot.py:61`); lint via
   `python -m scripts.skills_lint [--dir skills]`
   (`scripts/skills_lint.py:91-93`).

### 3.6 Tambah event / konsumen stream

- **Kontrak event:** model di `app/schemas/stream.py`, builder di
  `app/helpers/response.py` — tambah event = tambah model + builder +
  pakai di SATU tempat (`app/schemas/stream.py:1`,
  `app/helpers/response.py:1`). Jangan tulis dict event manual di
  controller/agent.
- **Pemetaan graph→SSE** terpusat di `Runner._map_stream_event`
  (tak pernah raise) (`app/controllers/runner.py:328`); `result` final
  dibangun dari checkpoint (`app/controllers/runner.py:358`).
- **Konsumen = `turns.follow`:** SSE tail (`app/api/v1/routes/chat.py:105`),
  SSE resume dari cursor (`app/api/v1/routes/chat.py:151`), WS pump/resume
  (`app/api/v1/routes/ws.py:1`). TurnManager API: `start`, `follow`,
  `is_owner`, `enabled` (`app/core/turns.py:33`).
- **Terminal:** `result`/`error` menandai akhir; `is_terminal()` satu
  sumber (`app/infra/turnlog.py:29`, `app/infra/turnlog.py:56`).
  Pengecualian: gagal PRE-RUN = satu `error` TANPA `started`
  (`app/schemas/stream.py:11`).
- **Bila event butuh policy baru:** tambah jenis ke `CLIENT_EVENT_KINDS`
  dengan sadar (default tolak) dan uji regresi kebocoran
  (`app/core/stream_policy.py:56`, `app/core/stream_policy.py:39`).

## §4 Kontrak API

Prefix API: `/api/v1` (`app/main.py:312`, `app/core/config.py`: `API_V1_PREFIX`).
Agregasi router: `app/api/v1/router.py:16-24`.

### 4.1 Konvensi umum

### 4.1.1 Auth & Scope

- Semua endpoint bisnis butuh `Authorization: Bearer <access_token>`, kecuali
  `/livez`, `/ready`, `/auth/login`, `/auth/refresh` (publik).
- Gate scope per-router via `require_scope_dep` (`app/core/dependencies.py:122-136`;
  cek ketat di `app/core/auth.py:185-193`): scope kosong = tolak 403.
- Scope terpakai: `chat` (chat), `jobs`, `threads`, `approve` (approvals+audit),
  `documents`. Endpoint tanpa gate scope: logout/me; `/health` BUTUH JWT via
  `get_current_claims` tanpa gate scope (`app/api/v1/routes/health.py:198-200`).
- `clearance` (level baca maks, v1.0 G6): dibawa di klaim JWT, dibaca via
  `/auth/me` (`app/schemas/auth.py:36-42`); dipakai filter retrieval + guard upload dokumen.

### 4.1.2 Error RFC 7807 + `code`

- Bentuk: `{type, title, status, detail, instance, code}` — `status` SELALU ==
  HTTP status; `instance` = request_id. Acuan: `app/schemas/common.py:10-18`;
  mapping: `app/core/errors.py:1-6`, handler `app/core/errors.py:318-327`.
- Validasi Pydantic → 422 `VALIDATION_ERROR` + ekstensi `errors[]` per-field
  (`app/core/errors.py:418-446`). 500 digenerikkan ("Terjadi kesalahan internal"),
  detail penuh hanya di log server (`app/core/errors.py:330-343`).
- Contoh gagal: `POST /jobs/x` agen tak dikenal → `404 AGENT_NOT_FOUND`
  (`app/core/errors.py:36-43`).
- Gotcha: JANGAN pernah 200 berisi error — builder sukses hanya untuk sukses
  (`app/helpers/response.py:9-15`).

### 4.1.3 `meta` Sukses + `X-Request-ID`

- Tiap respons sukses sync WAJIB lewat `job_success()` agar ada `meta: {request_id,
  duration_ms}` (`app/helpers/response.py:46-55`; skema `app/schemas/common.py:21-25`).
- Middleware echo/generate `X-Request-ID` di semua respons (`app/core/request_id.py:31-38`).
- Gotcha: kirim `X-Request-ID` sendiri untuk korelasi log ↔ respons error (`instance`).

### 4.1.4 Idempotency-Key

- Header `Idempotency-Key` didukung `POST /jobs/{agent}` (`app/api/v1/routes/jobs.py:60-84`)
  dan `POST /documents` (`app/api/v1/routes/documents.py:119-165`).
- State: `REPLAY` (respons lama + header `Idempotent-Replayed: true`), `CONFLICT`
  (409 `IDEMPOTENCY_KEY_REUSED`, payload beda), `IN_PROGRESS` (409
  `IDEMPOTENCY_IN_PROGRESS`) — `app/api/v1/routes/jobs.py:75-84`.
- Kunci dilepas saat eksekusi gagal agar boleh retry (`app/api/v1/routes/jobs.py:94-99`).
- Gotcha: tanpa cache → degradasi tanpa dedup + flag `X-Degraded: idempotency`
  (`app/api/v1/routes/jobs.py:71-74`); klien WAJIB retry hati-hati.

### 4.1.5 Rate Limit

- Pola: dependency per-route `rate_limit_dependency(scope, limit_attr)`
  (`app/core/ratelimit.py:107-133`); login/refresh punya checker khusus per-IP
  (`app/core/ratelimit.py:138-174`). Tolak → 429 `RATE_LIMITED` + header
  `Retry-After` + `X-RateLimit-Limit` (`app/core/errors.py:184-192`).
- Default limit (`app/core/config.py`): chat 20, jobs 10, threads 30, approvals 10,
  ingest 5, retrieval 30, login 5, refresh 30, WS ticket 10, WS chat 20, WS resume 60 (per menit).
- Gotcha: respons sukses TIDAK membawa header sisa kuota (hanya 429 yang bawa
  `Retry-After` + `X-RateLimit-Limit`, `app/core/errors.py:190-191`) —
  klien backoff hanya dari 429 + `Retry-After`.

### 4.1.6 Paginasi & Envelope Umum

- Pola beku: query `limit` + `offset` → respons `{items[], total?, limit, offset}`.
  Contoh: approvals (`app/api/v1/routes/approvals.py:47-58`), history
  (`app/api/v1/routes/threads.py:41-53`), audit (`app/api/v1/routes/approvals.py:86-101`).
- Bentuk luar respons = bare (tanpa envelope `data`), diputuskan bareng
  (`app/helpers/response.py:9-10`).
- Gotcha: `total` di list approvals = panjang halaman, BUKAN total global
  (`app/api/v1/routes/approvals.py:57-58`) — jangan pakai untuk page count.

### 4.2 Auth JWT

Skema: `app/schemas/auth.py:1-42`. Route: `app/api/v1/routes/auth.py:38-84`.
Controller: `app/controllers/AuthController.py:1-134`.

### 4.2.1 `POST /auth/login` → 200 `TokenPair`

- Auth: publik, rate-limit per IP+username SEBELUM verifikasi (`app/api/v1/routes/auth.py:46-48`).
- Request: `{workspace, username, password}` (pola regex di `app/schemas/auth.py:9-16`).
- Sukses: `{access_token, refresh_token, token_type: "bearer", expires_in}`
  (`app/schemas/auth.py:19-23`); TTL access default 900 dtk, refresh 7 hari (`app/core/config.py`).
- Gagal: 401 generik "Kredensial tidak valid." (anti-enumerasi — user-ada vs
  password-salah tak dibedakan), 429 bila limit (`app/controllers/AuthController.py:50-66`).
- Contoh: `{"workspace":"toko-a","username":"budi","password":"..."}`.
- Gotcha: username/workspace dinormalisasi via `canon_identity` (lapis-2)
  (`app/controllers/AuthController.py:51-55`).

### 4.2.2 `POST /auth/refresh` → 200 `TokenPair` (rotasi)

- Auth: publik + refresh token di body, rate-limit per IP (`app/api/v1/routes/auth.py:62-64`).
- ROTASI: token lama diklaim atomik sekali-pakai (SET NX) SEBELUM token baru
  terbit; replay/revoke → 401; cache mati → 503 fail-closed
  (`app/controllers/AuthController.py:77-91`).
- Gagal: 401 `TOKEN_EXPIRED`/`INVALID_TOKEN`, 401 bila user nonaktif, 429, 503.
- Gotcha: refresh yang dicuri hanya bisa dipakai sekali — klien WAJIB simpan
  pair BARU tiap refresh dan buang yang lama.

### 4.2.3 `POST /auth/logout` → 204

- Auth: Bearer WAJIB. Body opsional `{refresh_token}` untuk ikut revoke refresh
  (best-effort) (`app/api/v1/routes/auth.py:67-76`, `app/controllers/AuthController.py:94-108`).
- Efek: revoke access (+ refresh bila dikirim) selama sisa umur; WS ikut
  diputus ≤1 heartbeat via cek revoke (`app/api/v1/routes/ws.py:259-274,441-458`).
- Gotcha: logout TANPA body hanya revoke access — refresh yang bocor tetap hidup
  sampai kedaluwarsa; selalu kirim refresh_token bila ada.

### 4.2.4 `GET /auth/me` → 200 `MeResponse`

- Auth: Bearer. Respons: `{workspace, user, scopes[], expires_at, clearance}`
  (`app/controllers/AuthController.py:111-115`).
- Gotcha: `expires_at` = epoch detik (bukan ISO) — samakan jam klien untuk countdown.

### 4.3 SSE Chat

Route: `app/api/v1/routes/chat.py:65-160`. Scope: `chat` (`app/api/v1/routes/chat.py:40-41`).

### 4.3.1 `POST /chat/stream` → 200 `text/event-stream`

- Auth: Bearer scope `chat` + rate-limit chat (`app/api/v1/routes/chat.py:72`).
- Request `ChatRequest` (`app/schemas/chat.py:10-22`): `{agent="example_chat",
  thread_id?="ws:user:sess", input (1-8000 char), metadata?}`.
  `thread_id` kosong = server susun dari JWT; format lama (`thr-xxx`) ditolak 400.
- Preflight PENUH sebelum turn ada: resolve thread + agen-ada + build agen +
  guard interrupt → gagal = JSON biasa (400/403/404/409/502/503), TANPA turn yatim
  (`app/api/v1/routes/chat.py:74-78`; `app/controllers/ChatController.py:88-102`).
- Sukses: header `X-Turn-ID` + `X-Request-ID` (`app/api/v1/routes/chat.py:118-121`).
  Gagal pre-run khas: 404 `AGENT_NOT_FOUND`, 409 `THREAD_AWAITS_APPROVAL`
  (`app/core/errors.py:88-98`), 400 `INVALID_THREAD_ID`, 403 cross-workspace.
- Contoh: `POST /api/v1/chat/stream {"agent":"example_chat","input":"stok ITEM-99?"}`.
- Gotcha: JANGAN parse body sebagai JSON — ini stream SSE; simpan `X-Turn-ID`
  untuk resume.

### 4.3.2 Tipe Event (7, BEKU) + Envelope

- Skema: `app/schemas/stream.py:26-89`. Builder: `app/helpers/response.py:96-155`.
- Urutan: `started → text*/thinking*/tool_start/tool_end → result | error(terminal)`
  (`app/schemas/stream.py:3-5`). Field event:
  - `started {turn_id?}` (`:26-30`); `text {delta}` (`:33-37`);
    `thinking {delta}` (`:40-48` — tampilkan collapsible TERPISAH, jangan gabung jawaban);
    `tool_start {tool, input}` (`:51-56`); `tool_end {tool}` (`:59-63` — output SENGAJA absen);
    `result {status, output, interrupt?, interrupts?, trace?}` (`:66-77`);
    `error {title, status, detail, instance, code}` ikut RFC 7807 (`:80-89`).
- ENVELOPE transport: controller membungkus tiap event
  `{"thread_id":..., "request_id":..., **event}` (`app/schemas/stream.py:7-9`;
  rakit `app/controllers/ChatController.py:157`). Konsumen strict: parse envelope dulu.
- TERMINATOR = stream close; TIDAK ada baris `[DONE]` (`app/schemas/stream.py:4-5`).
- Pengecualian jujur: gagal PRE-RUN = SATU event `error` TANPA `started`;
  bila `started` sudah keluar, penutup TEPAT SATU `result|error` dijamin
  (`app/schemas/stream.py:11-14`).
- Gotcha: model non-reasoning tak memancarkan `thinking` (normal, bukan error).

### 4.3.3 Resume `GET /chat/stream/{turn_id}` + `Last-Event-ID`

- Frame SSE ber-`id:` = cursor stream (`app/api/v1/routes/chat.py:48-50`);
  resume kirim header `Last-Event-ID: <cursor>` (default `"0-0"`)
  (`app/api/v1/routes/chat.py:124-160`).
- Replay byte-identik dari turn log lalu ikuti sampai terminal
  (`app/api/v1/routes/chat.py:151-156`). TTL turn 900 dtk berjalan, 300 dtk
  setelah selesai (`app/infra/turnlog.py:31-32`).
- Gagal: 410 `STREAM_GONE` bila log hilang/kedaluwarsa/salah-pemilik
  (salah-pemilik = 410 SAMA, anti-oracle; `app/api/v1/routes/chat.py:142-148`);
  503 `STORAGE_UNAVAILABLE` bila cache down (resume BUTUH log — tak bisa degradasi).
- Gotcha: tanpa cache (`CACHE_URL` kosong) jalur POST degradasi ke direct-stream
  TANPA `id:` dan TANPA resume (`app/api/v1/routes/chat.py:81-83,163-189`);
  cache mati di tengah → fail-open direct + header `X-Degraded` (`:93-100`).

### 4.3.4 `trace` di `result` + TRACE-CONTRACT

- `result.trace = {trace_version:1, complete, spans[≤20], truncated,
  pending_approvals?}` — kontrak BEKU `docs/TRACE-CONTRACT.md:1-64`.
- Span: `{tool, kind: tool|subagent|skill, status: ok|partial|failed|timeout|
  skipped|pending, ms, attempts, input_summary≤500, detail≤500}` (disanitasi +
  redaksi secret; `docs/TRACE-CONTRACT.md:29-36`).
- Otoritas: **trace > kartu (`interrupts[]`) > reply** (`docs/TRACE-CONTRACT.md:86-102`):
  reply boleh karangan; `complete=False` + `pending_approvals=n` = jeda sah.
- Aturan: terminal `error` TANPA trace; DILARANG fabrikasi trace dari
  `tool_start/tool_end`; DILARANG jumlah `ms` lintas span (paralel + span ganda)
  (`docs/TRACE-CONTRACT.md:104-123`).
- Gotcha: `trace: null` hanya di jalur fallback eksotik — sinyal jeda jatuh ke
  `status="pending_approval"` + kartu; `null` BUKAN bukti tak ada approval
  (`docs/TRACE-CONTRACT.md:99-102`).

### 4.4 WebSocket Realtime

Route: `app/api/v1/routes/ws.py:1-118`. Alur 6 langkah di docstring (`:1-18`).

### 4.4.1 `POST /realtime/ticket` → 200 `{ticket, expires_in, ws_url, subprotocol}`

- Auth: Bearer scope `chat` (dicek manual — handshake WS tanpa Bearer) +
  rate-limit per user (`app/api/v1/routes/ws.py:57-75`).
- Ticket sekali-pakai, TTL 30 dtk (`app/core/config.py`: `WS_TICKET_TTL_SECONDS`);
  butuh cache → cache down = 503 jujur (`app/api/v1/routes/ws.py:81-85`).
- Contoh: `POST /api/v1/realtime/ticket` → sambung
  `WS /api/v1/ws/chat?ticket=...` (`app/api/v1/routes/ws.py:86-88`).
- Gotcha: ticket hangus sekali pakai — reconnect butuh ticket BARU.

### 4.4.2 Handshake `WS /ws/chat` + Subprotocol

- Subprotocol: `ai-app.v1` (`app/api/v1/routes/ws.py:48`); Origin divalidasi
  (CSWSH) — tolak = close 1008 (`app/api/v1/routes/ws.py:91-101`).
- Ticket invalid → close 1008; `WS_ENABLED=false` → close 1013
  (`app/api/v1/routes/ws.py:93-108`). Sukses → server kirim `ready
  {subprotocol, expires_in, heartbeat}` (`app/api/v1/routes/ws.py:166-168`).
- Umur koneksi maks 1800 dtk → frame `auth_expired` + close 4001 (reconnect +
  resume) (`app/api/v1/routes/ws.py:52,180-190`); logout (revoke) memutus ≤1
  heartbeat (`:441-458`).
- Close-code: 1008 policy, 1013 try-again (klien lambat/penuh), 1012 restart,
  4001 sesi habis (`app/api/v1/routes/ws.py:49-52`).
- Gotcha: jadwalkan re-ticket + reconnect + resume SEBELUM `expires_in` habis.

### 4.4.3 Pesan Klien: chat / resume / cancel / ping

- `{"type":"chat","agent","input","thread_id?"}` → preflight (gagal = frame
  error bersih, tanpa turn yatim) → `ack {turn_id}` KILAT → pompa event envelope
  (`app/api/v1/routes/ws.py:275-334`). BUSY 409 bila run aktif (`:276-281`).
- `{"type":"resume","turn_id","last_event_id?"}` → pompa turn log dari cursor,
  paritas HTTP; salah-pemilik/hilang = frame 410 `STREAM_GONE`
  (`app/api/v1/routes/ws.py:391-424`).
- `{"type":"cancel"}` → hentikan run aktif → `cancelled`
  (`app/api/v1/routes/ws.py:212-215`); `{"type":"ping"}` → `pong` (`:210-211`).
- Tiap pesan `chat/resume` di-gate scope + rate-limit (paritas HTTP);
  ditolak = frame error 403/429 (`app/api/v1/routes/ws.py:231-257`).
  Pesan tak dikenal → frame 400 `BAD_MESSAGE` (`:225-229`).
- Gotcha: SATU run per koneksi — `resume` saat run aktif = 409 `BUSY`, kirim
  `cancel` dulu (`app/api/v1/routes/ws.py:391-401`).

### 4.4.4 Backpressure, `seq`, Frame Server

- Tiap frame server ditambah `seq` monotonik per koneksi
  (`app/api/v1/routes/ws.py:463-466`). Envelope event SAMA dengan SSE + `seq`.
- Queue per koneksi `WS_MAX_QUEUE` (default 256): penuh → frame `text`/`thinking`
  DI-DROP (aman — bisa resume), frame terminal tak pernah di-drop; klien yang
  tetap lambat diputus 1013 (`app/api/v1/routes/ws.py:54,427-439`).
- Heartbeat server tiap `WS_HEARTBEAT_SECONDS` (25 dtk): `{type:"heartbeat",ts}`
  (`app/api/v1/routes/ws.py:441-458`). Disconnect = pump berhenti, produsen jalan
  terus (paritas resume HTTP) (`app/api/v1/routes/ws.py:336-341`).
- Gotcha: gap `seq` tanpa terminal = frame droppable hilang — resume via
  `turn_id` untuk state utuh, jangan asumsikan text lengkap.

### 4.5 Jobs Sync

- `POST /jobs/{agent_name}` → 200 `JobResponse` (`app/api/v1/routes/jobs.py:45-106`).
- Auth: Bearer scope `jobs` + rate-limit jobs (`app/api/v1/routes/jobs.py:39-55`).
- Request `JobRequest` (`app/schemas/job.py:12-24`): `{thread_id?, payload{},
  metadata?}`. Cap payload 256 KB → 413 (`app/api/v1/routes/jobs.py:109-112`).
- Respons `JobResponse` (`app/schemas/job.py:27-41`):
  `{thread_id, status, output{reply|data?}, interrupt?, meta}`.
- Gagal: 404 agen, 409 idempotency (`IDEMPOTENCY_KEY_REUSED`/`IN_PROGRESS`),
  413, 429, 502 upstream AI, 500 kontrak rusak (`app/api/v1/routes/jobs.py:86-94`).
- Contoh: `POST /api/v1/jobs/contoh {"payload":{"file_url":"https://..."}}`.
- Gotcha: `output.data` OPSIONAL — absen saat fallback deterministik aktif;
  konsumen WAJIB tangani tanpa error (`app/schemas/job.py:30-39`).

### 4.6 Threads

Route: `app/api/v1/routes/threads.py:28-78` (scope `threads` + rate-limit,
`:22-25`). Skema: `app/schemas/threads.py:20-72`.

- `GET /threads/{thread_id}/state` → `ThreadStateResponse` (checkpoint, step,
  preview pesan, `next[]`, `interrupt_ids[]`); query `last_n` 1-200
  (`app/api/v1/routes/threads.py:28-38`; `app/schemas/threads.py:27-41`).
- `GET /threads/{thread_id}/history` → `HistoryResponse {items[], limit, offset,
  total}`; `limit` 1-100, `offset` ≥0 (`app/api/v1/routes/threads.py:41-53`).
- `POST /threads/{thread_id}/fork` ← `{checkpoint_id, agent}` →
  `ForkResponse {thread_id BARU, forked_from, agent}` (`app/api/v1/routes/threads.py:56-67`;
  `app/schemas/threads.py:60-72`).
- `DELETE /threads/{thread_id}` → 204 (`app/api/v1/routes/threads.py:70-78`).
- Gagal: 400 ID invalid, 403 asing, 404 `THREAD_NOT_FOUND`, 429.
- Gotcha: `thread_id` mengandung `:` — FE WAJIB URL-encode (`%3A`)
  (`app/api/v1/routes/threads.py:3-4`); isi pesan di-cap 2000 char
  (`truncated=true`); pesan infrastruktur disembunyikan (`app/schemas/threads.py:9-12`).

### 4.7 HITL Approvals + Audit

Route: `app/api/v1/routes/approvals.py:47-110` (scope `approve` + rate-limit,
`:32-36`). Skema: `app/schemas/approvals.py:10-69`.

- `GET /approvals?status=pending&limit=&offset=` → `ApprovalListResponse`
  (`app/api/v1/routes/approvals.py:47-58`).
- `GET /approvals/{approval_id}` → `ApprovalResponse` (termasuk `action_hash`,
  `action_snapshot`, `policy_version`) (`app/api/v1/routes/approvals.py:61-68`).
- `POST /approvals/{approval_id}/decide` ← `{decision: approve|reject, reason?≤500}`
  → `DecideResponse {approval_id, status, resumed, result?}` — keputusan
  MEMICU RESUME graph, bukan sekadar tandai baris (`app/api/v1/routes/approvals.py:71-83`).
- `GET /audit?limit=&offset=` → `AuditListResponse` (hash chain);
  `GET /audit/verify` → `{ok, first_bad_id?}` (`app/api/v1/routes/approvals.py:86-110`).
- Gagal: 403 tanpa scope `approve`, 404, 409 `APPROVAL_ALREADY_DECIDED`
  (`app/core/approvals.py:45`), 429.
- Contoh: `POST /api/v1/approvals/12/decide {"decision":"approve"}`.
- Gotcha: requester lacak via thread miliknya, BUKAN daftar ini (area reviewer)
  (`app/api/v1/routes/approvals.py:29-31`); chat baru di thread menunggu approval
  ditolak 409 `THREAD_AWAITS_APPROVAL` — decide dulu (`app/core/errors.py:88-98`).

### 4.8 Health

Route: `app/api/v1/routes/health.py:198-230`.

- `GET /livez` publik, tanpa I/O, selalu 200 bila proses hidup — SATU-SATUNYA
  untuk K8s liveness (`:209-214`).
- `GET /ready` publik MURAH: probe DB cepat; 503 bila DB tak terjangkau
  (`:217-230`). Cache/RAG down TIDAK membuat 503 (fail-open).
- `GET /health` BUTUH JWT: status penuh + versi/model (rekonesaisi bila publik)
  (`:198-206`). Tak pernah 500 — laporkan `degraded`.
- Gotcha: K8s liveness JANGAN ke `/health` (bisa restart pod yang hidup saat
  downstream gantung) — pakai `/livez` (`app/api/v1/routes/health.py:202-205`).

### 4.9 Dokumen & Retrieval (RAG)

Route: `app/api/v1/routes/documents.py` (scope `documents`, `:59-60`).
SSE progress pakai skema SENDIRI (`ingest_started/progress/result/error`),
BUKAN envelope chat (`:6-8`).

- `POST /documents` (multipart: `file, preset=prose, ocr=auto, lang=id,
  access_level=public`) → 202 `IngestEnqueueResponse {document_id, job_id,
  status:"queued", deduped}` (`:86-165`; skema `app/schemas/documents.py`).
  Lapis: rate-limit ingest → cap 413 → sniff MIME 415 → Idempotency-Key → 202
  (`:3-4`). `access_level` WAJIB ⊆ clearance uploader (403 bila tidak)
  (`:102-113`).
- `GET /documents` → `DocumentListResponse`; `GET /documents/{id}` →
  `DocumentResponse`; `DELETE /documents/{id}` → 202 (`:168,:196,:209`).
- `GET /documents/{id}/progress` → SSE (`:228-317`);
  terminal HANYA `done/dead` — `failed` = terjadwal retry, stream TERUS
  (`:65-69`); idle 15 mnt = tutup + reconnect (`:69`).
- `GET /ingestion_jobs/{job_id}` → `JobStatusResponse`; `POST .../retry` → 202
  (`:321,:336`). `POST /retrieval/search` ← `{query, k≤20, filters?}` →
  `{matches[CitationResponse], meta}` (`:359`).
- Gagal khas: 404 `DOCUMENT_NOT_FOUND` (anti-enumerasi), 415 `UNSUPPORTED_MEDIA`,
  409 `INGEST_CONFLICT`/`OCR_REQUIRED`, 410 `INGEST_GONE`, 422 `OCR_FAILED`,
  503 `EMBEDDING_UNAVAILABLE` (`app/core/errors.py:219-316`).
- Gotcha: `deduped=true` = doc lama menang, blob baru dibuang, job
  reuse-or-create anti job ganda — jangan tampilkan sebagai upload baru
  (`app/controllers/DocumentController.py:89-100`).

## §5 Subsistem RAG

Cakupan baca: `app/rag/*`, `app/infra/rag/*`, `app/workers/*`, `evals/*`, `app/core/config.py`.

### 5.1 Kontrak & batas lapis

- `app/rag` dilarang impor `app/infra`, `httpx`, `qdrant_client`, `psycopg`, `fastapi`, SDK model; murni Python+pydantic — `app/rag/ports.py:3-7`.
- Workspace dikunci saat CONSTRUCTION (factory-bound, D2); method query tak menerima argumen workspace — `app/rag/ports.py:5-7`, `app/rag/ports.py:201-202`.
- `VectorStorePort` adalah primitif per-backend; `RAG_BACKEND` mengganti ini, bukan seluruh retrieval — `app/rag/ports.py:11-12`.
- `BoundRetriever` hasil factory adalah satu-satunya yang dipakai agen — `app/rag/ports.py:13`, `app/rag/ports.py:246-258`.
- Kontrak ingest: `SourceConnector`/`OCREngine`/`DocumentParser` — `app/rag/ports.py:14`; kontrak graph v0.8: `GraphStore`+`EntityExtractor`+`GraphRanker` — `app/rag/ports.py:15-16`.
- `CitedChunk.score` = skor final pasca-RRF/rerank (ordinal) — `app/rag/ports.py:26-40`; `kind` absent = `"vector"` — `app/rag/ports.py:39`.
- `ChunkIn.content_norm` = lexical ternormalisasi; `None` = mentah — `app/rag/ports.py:43-54`.
- `RerankedDoc.score` ordinal, bukan probabilitas — `app/rag/ports.py:57-59`.
- `RetrievalTrace` selalu dikumpulkan (timing+hitungan+skor ringkas) — `app/rag/ports.py:62-82`; `grounded=False` = skor skala-RRF tanpa rerank, konsumen wajib banner — `app/rag/ports.py:90-92`.
- `IndexInfo` untuk validasi anti-campur D4/D6 — `app/rag/ports.py:95-103`.

### 5.2 Arsitektur retrieval hybrid

- Alur beku (D1): embed → dense_top=50 + lexical_top=50 [+graph] → RRF(k=60) → candidate_top=20 → truncasi batas kalimat (128/384/512) → rerank batch → final_top=5 (+floor → no_evidence; rerank timeout → degraded) — `app/rag/pipeline.py:1-6`.
- Murni: tanpa I/O selain via Port yang di-inject; deterministik penuh untuk input yang sama — `app/rag/pipeline.py:10-11`.
- `HybridRetriever` dibangun SUDAH terikat workspace; `store` sudah bound, pipeline tak tahu workspace — `app/rag/pipeline.py:101-105`.
- Embed query dipotong dulu (hemat konteks BGE-M3); gagal = raise, tanpa fallback beda-model (D6) — `app/rag/pipeline.py:239-243`.
- Fan-out dense→lexical SERIAL sengaja (`asyncio` dilarang di `app/rag`); biaya ~2x RTT diterima — `app/rag/pipeline.py:245-251`.
- Jejak per-request: backend, timings, counts, top_score, floor, status, degraded, query_preview — `app/rag/pipeline.py:211-237`.
- RRF: skor = Σ 1/(k+rank), rank 1-based; chunk 1-sisi tetap dihitung 1 suku — `app/rag/pipeline.py:44-49`. Deterministik: urut (-skor, rank_dense, rank_lexical, rank_graph, chunk_id) — `app/rag/pipeline.py:47`, `app/rag/pipeline.py:70`. Graph `None`/kosong = perilaku 2-way v0.7 identik — `app/rag/pipeline.py:7`, `app/rag/pipeline.py:46-51`.
- Truncasi di BATAS KALIMAT; fallback 1 kalimat > batas → potong per kata — `app/rag/pipeline.py:79-98`. Budget dokumen = `doc_max`, dikurangi query bila pair melebihi batas; min 32 — `app/rag/pipeline.py:442-448`.
- Rerank batch WAJIB (loop sekuensial dilarang, D5); timeout → builtin `TimeoutError` — `app/rag/ports.py:177-183`. Gagal APAPUN → degradasi eksplisit (RRF top-k + flag), BUKAN 500 — `app/rag/pipeline.py:369-370`, `app/infra/rag/infinity.py:187-193`.
- Respons rerank kosong/korup (index di luar jangkauan/NaN/bool) = `rerank_unavailable`+`rerank_corrupt`, BUKAN 500/no_evidence palsu — `app/rag/pipeline.py:392-415`.
- Floor → `no_evidence`: urutan provider tak dipercaya (sort sendiri); chunk di bawah floor dibuang — `app/rag/pipeline.py:389-434`. Floor ganda v0.8 (D12): natural vs verbal; `graph_score_floor=0.0` = TANPA floor graph — `app/rag/pipeline.py:417-432`.
- Pin model D6: `EmbeddingsPort` dilarang fallback beda-model — `app/rag/ports.py:157-170`. Pin embed `BAAI/bge-m3` + rerank `BAAI/bge-reranker-v2-m3` (Infinity v2 `/embeddings`+`/rerank`, BUKAN `/v1/*`) — `app/core/config.py:123-133`, `app/infra/rag/infinity.py:1-11`. Pengecualian F5b: fallback MODEL-IDENTIK via `FallbackEmbeddings` — `app/infra/rag/infinity.py:106-114`, `app/infra/rag/factory.py:95-129`; startup tolak bila beda nama/dims — `app/infra/rag/factory.py:101-126`.
- `RAG_BACKEND` = setting DEPLOY (`pgvector` default | `qdrant` opt-in); pindah backend = reindex eksplisit — `app/core/config.py:119-121`. Nama koleksi Qdrant mengandung dim+model — `app/infra/rag/qdrant.py:26-29`.
- PG: dense HNSW + lexical tsvector; vektor dikirim sebagai string `'[..]'::vector`; tak pernah baca embedding kembali — `app/infra/rag/pgvector.py:1-9`. Lexical dual-cabang: `tsv_norm`+query normalisasi ATAU `tsv`+query mentah (tanpa backfill, BitmapOr 2 index GIN) — `app/infra/rag/pgvector.py:146-180`.
- Qdrant lexical = boolean `MatchText` + ranking BM25-lite client-side — `app/infra/rag/qdrant.py:179-221`, `app/rag/rank.py:1-11`. BM25-lite: IDF query-lokal, avgdl kandidat, K1=1.2/B=0.75, tiebreak idx stabil — `app/rag/rank.py:17-53`.
- PG adalah SoR; Qdrant turunan rebuildable; reconciler `python -m app.workers.reindex` bandingkan by `point_id` deterministik — `app/workers/reindex.py:1-9`, `app/infra/rag/qdrant.py:32-34`. Guard anti-hapus-massal, `--since` inkremental, butuh role BYPASSRLS — `app/workers/reindex.py:91-96,138-148,198-212`.
- Runbook cutover prod BELUM ADA (gap dokumentasi); satu-satunya kriteria: selisih=0 di `app/workers/reindex.py:5-6`.

### 5.3 Normalisasi Bahasa Indonesia (sisi lexical saja)

- HANYA sisi lexical (query + kolom `content_norm`); dense/rerank tetap teks mentah — `app/rag/normalize.py:16-17`.
- Query: `normalize_id(query, stem)` + `raw_query=query` untuk fallback baris lama — `app/rag/pipeline.py:253-255`, `app/rag/ports.py:223-235`. Index: worker menulis `content_norm=normalize_id(content, stem=RAG_STEMMER)` — `app/workers/ingest.py:593-595`. `RAG_STEMMER` WAJIB sama index dan query — `app/core/config.py:158-160`.
- Urutan: fold NFKD+lowercase → ekspansi angka-skala (APPEND) → tanda baca→spasi → buang stopword → stemming Sastrawi per token — `app/rag/normalize.py:8-14`. Deterministik, idempoten, total — `app/rag/normalize.py:83-96`.
- Stopword kata tanya + fungsi, lowercase, DO NOT STEM — `app/rag/normalize.py:25-37`. Token 1-char dibuang kecuali digit — `:91-92`. Ekspansi `35 ribu` → `35 ribu 35000`, idempoten — `:58-80`. Stemming lazy-singleton, opt-out `stem=False` — `:48-55,93-95`.
- `normalize_entity` (graph, BUKAN lexical): lower+fold+rapikan SAJA; worker dan adapter WAJIB fungsi sama persis — `app/rag/normalize.py:99-112`.

### 5.4 Ingest worker

- Entrypoint `python -m app.workers.ingest`, proses terpisah; Postgres-queue + lease + stage machine — `app/workers/ingest.py:1-3`. Pool SENDIRI, klaim transaksi pendek, DILARANG HTTP di dalam `pool.connection()` — `:5-6`, `app/core/config.py:232-241`.
- Progress: SoR Postgres + ekor Valkey best-effort TTL 3600s — `:12-14,179-198`. Lease 120s + heartbeat 30s; dicuri (2x miss) = BERHENTI tanpa `fail_job` — `:8,81-82,248-295`.
- Parser routing by MIME sniff magic-number, BUKAN ekstensi — `app/rag/parsers/sniff.py:1-56`. Peta: PDF/DOCX/PPTX→Docling, MD/TXT→Markdown, XLSX→Xlsx — `app/workers/ingest.py:133-146`. XLSX tolak macro/VBA + guard `MAX_XLSX_CELLS` sebelum baca penuh — `app/infra/rag/xlsx.py:1-54`.
- OCR jalur terpisah: probe pypdf murah dulu; PDF pindai + `ocr!=on` → dead + minta aksi klien; Tesseract DPI 300 `ind+eng`; confidence <0.6 → meta `low_ocr` — `app/infra/rag/pdf_probe.py:1-31`, `app/infra/rag/tesseract.py:1-76`, `app/workers/ingest.py:479-503`.
- Chunker D8 beku 4 preset (prose/markdown/pdf 512/64; factoid 256/0); satuan token = tokenizer BGE-M3 inject — `app/rag/chunker.py:3-41`. Markdown header-aware H2/H3; PDF per-halaman selalu; factoid 1 baris = 1 chunk — `:132-204`, `app/workers/ingest.py:517-522`. `CHUNKER_VERSION=v1` masuk `chunk_id` sha256 stabil — `:43-45`, `app/rag/citations.py:17-27`.
- Konektor DB read-only + watermark `(updated_at,id)`: TANPA method tulis by construction, transaksi `READ ONLY`, denylist kata tulis, allowlist tabel — `app/rag/ports.py:261-278`, `app/infra/rag/connectors/postgres.py:1-52`. Secret Fernet `SOURCE_CONN_KEY` — `app/infra/rag/secrets.py:1-26`. **Konektor BELUM dipakai jalur produksi v2.0.0** (`fetch_incremental` tanpa pemanggil di `app/` — hanya kontrak+implementasi+tes); belum ada scheduler/API sync.
- Stage: parsing(10) → chunking(40) → embedding(60) → indexing(85) → graph(95) — `:473-625`. Embed batch 32; vektor nol = terminal `BAD_VECTOR` — `:32,48-55,564-566`. Retry `5s*2^attempts+jitter`, cap 5 mnt, max 5x → dead — `:9-10,41-45`. Terminal langsung: `UNSUPPORTED_MEDIA`, `OCR_REQUIRED`, `OCR_FAILED`, `BLOB_GONE`, `EMPTY_DOCUMENT`, `DOCUMENT_GONE` — `:85-114`.
- Kuota graph `GRAPH_QUOTA_EDGES_PER_WORKSPACE` (default 500rb); lewat = stage graph dilewati, vektor tetap jalan — `:330-341`. Batas: 20MB upload, 200 hal PDF, 200rb sel XLSX; tolak 413 SEBELUM enqueue — `app/core/config.py:137-141`.

### 5.5 Knowledge graph (PG-native, doc-scoped)

- Core beku `kg_onto/v1`: 10 tipe node + 15 relasi; ekstensi `workspace:*` terdaftar — `app/rag/ontology.py:1-28`. Relasi tak dikenal → fallback `related_to` + force-fit — `:54-63`.
- Traversal = BFS iteratif bounded di Python, BUKAN recursive CTE — `app/infra/rag/pg_graph.py:7-9`. Cap ≤200 path, ≤50 node; tolak sikel — `:41-46,354-411`. Skor confidence × penalti derajat × diskon hop-2 0.7 — `:379-397`. Timeout → `graph_unavailable`, TIDAK PERNAH 500 — `:11-12,344-345`. Filter = `access_level` SAJA — `:277-288`.
- Seed deterministik tanpa model: quote → caps → n-gram → token → full query; preseed alias longest-first — `app/rag/kg_seed.py:3-77`. Hop-2 hanya bila hop-1 kurus (<3) — `app/rag/pipeline.py:315-324`.
- Verbalize: 1 triple = 1 kalimat, DILARANG klaim transitivitas; `VERBALIZE_VERSION=gv1` — `app/rag/verbalize.py:3-61`.
- Ekstraksi L1 (aturan) + L2 (GLiNER) selalu jalan, union prioritas L1; L3 (LLM) hanya bila L2 menyerah DAN chunk sensitif; gagal L3 → fallback L2 — `app/infra/rag/extract/layered.py:1-137`.

### 5.6 Eval RAG

- CLI: `python -m evals.run --suite rag_answer --tags canary`; exit 0/1/2 — `evals/run.py:1-6`. Suite: hitl_demo, pipeline, rag_answer, chat_tools, rag_chat — `:19`.
- Canary = tier PR deterministik tanpa live; baseline 17/17 seed 7; regresi = PR gagal — `tests/test_eval_canary.py:1-3`, `evals/baselines/canary.json:14-27`, `tests/test_eval_baseline.py:15-21`.
- gold30: 30 QA di pgvector+qdrant; satu-satunya assertion = canary faktoid 5/5, sisanya dicatat untuk gerbang — `tests/integration/test_gold30_live.py:1-10,284-286`. gold-graph 28: gate vguard + FPR=0 — `tests/integration/test_graph_gold_live.py:5-6`.
- Tambah kasus JSONL v1: `{"v":1,"id","agent","input","expect"}` + `tags`; subset-match — `evals/common/schema.py:1-97`. Baseline: `--record`/`--compare`; judge LLM `--judge-rate` default 0.0 observasi (K7) — `evals/common/runner.py:65-99`, `evals/run.py:118-119`.
- Artefak gold live di luar repo: `../Test/spike_v07/gold30_{backend}.json` (`tests/integration/test_gold30_live.py:22,266`); retensi CI tak terdokumentasi.

### 5.7 Kunci config `RAG_*`/`GRAPH_*` + fail-closed

- Retrieval: `RAG_RRF_K=60`, `DENSE/LEXICAL_TOP=50`, `CANDIDATE_TOP=20`, `FINAL_TOP=5` — `app/core/config.py:146-150`. Truncasi 128/384/512 — `:152-154`. Floor 0.05 (0.0=mati) — `:155-157`. Guard `FINAL_TOP > CANDIDATE_TOP` = error — `:379-382`.
- Embed/rerank URL+model; embed URL kosong = RAG nonaktif — `:123-133`, `app/infra/rag/factory.py:62-63`. Timeout 1500/2500ms — `:166-167`. Cold-start retry ~2 mnt, gagal = `RuntimeError` aksi — `factory.py:72-93`. `RAG_ALLOW_MIXED=false` tolak start bila index tak cocok pin — `:168-170`. `RAG_STRICT_STARTUP=true` (default): RAG gagal → tolak start; `false` = degraded, API non-RAG jalan (`app/main.py:188-200`).
- Graph: `GRAPH_ENABLED=false` default — `:173-174`. Traversal hops≤2, seed/expand/fanout/timeout — `:179-183`. Ekstraksi batch/entity/edge/conf — `:185-191`. L3 mati bila model kosong; kuota harian 500/ws — `:195-197,225-230`.
- Fail-closed ACL: clearance JWT → default filter; `access_level` panggilan di-INTERSECT — `app/rag/pipeline.py:135-192`, `app/infra/rag/factory.py:267-278`.

## §6 Ops

### 6.1 Prasyarat

- Python `3.12` — `.python-version` (`setup.ps1` membaca file ini; bila tak ada, default `3.12`).
- `uv` opsional (bila ada dipakai, bila tidak fallback `python -m venv/pip`) — `setup.ps1:16-35`.
- Docker Desktop — `../Infra/docker-compose.yml` + `README.md` §Mulai cepat.
- Git + PowerShell (Windows) — `setup.ps1`, `.gitattributes`.
- Node ≥18 — hanya untuk harness `TestFE`, bukan BE — `../TestFE/README.md` §1.

### 6.2 setup.ps1 (clone → jalan 1 perintah)

```powershell
.\setup.ps1              # install + pytest unit
.\setup.ps1 -SkipTests   # install saja
# Bila diblokir policy:
powershell -ExecutionPolicy Bypass -File .\setup.ps1   # setup.ps1:6-7
```

Yang dilakukan skrip (`setup.ps1:19-67`): buat `.venv` → install `requirements.txt` → salin `.env` → generate `JWT_SECRET` + `SOURCE_CONN_KEY` → (kecuali `-SkipTests`) install `requirements.dev.lock` + `pytest tests/ -m "not integration"`.

### 6.3 .env wajib

| Key | Wajib kapan | Catatan |
|---|---|---|
| `DATABASE_URL` | Selalu | Samakan user/pass/db dengan `APP_DB_*` di `Infra/.env` |
| `CACHE_URL` | Selalu diisi; kosong = fitur cache nonaktif | Diisi tapi tak terjangkau = app TOLAK start (fail-fast) |
| `LITELLM_API_KEY` + `LITELLM_MODEL` | Bila chat/jobs LLM dipakai | Virtual key per project, BUKAN master key |
| `JWT_SECRET` (≥32 char) | Selalu untuk `AUTH_MODE=jwt` | Digenerate `setup.ps1`; `ENVIRONMENT=prod` mewajibkan terisi |
| `QDRANT_API_KEY` | Bila `RAG_BACKEND=qdrant` | HARUS sama dengan `Infra/.env` |
| `METRICS_TOKEN` | Prod (v1.0) | Kosong = `/metrics` loopback saja |
| `TEST_DATABASE_URL` | HANYA OS environ, jangan di file | Dipakai tes integrasi (`tests/integration/conftest.py`) |

Aturan keras: `Settings` memakai `extra="forbid"` — typo/key asing = start GAGAL (`.env.example:1-2`). `ENVIRONMENT=prod` mewajibkan: `AUTH_MODE=jwt`, `JWT_SECRET` terisi, `CACHE_URL` terisi, `ENABLE_DOCS=false`.

### 6.4 Docker Infra (repo terpisah `../Infra`)

| Service | Compose | Port host | Keterangan |
|---|---|---|---|
| `app-db` | `docker-compose.yml` | `5433→5432` | Postgres+pgvector; checkpointer+store+RAG |
| `cache` | `docker-compose.yml` | `6380→6379` | Valkey; rate-limit/idempotency/denylist; ephemeral |
| `litellm` | `docker-compose.yml` | `4000` | Gateway LLM tunggal; UI `/ui` |
| `litellm-db` | `docker-compose.yml` | internal | Skema internal proxy; JANGAN disatukan dengan `app-db` |
| `phoenix` | `docker-compose.yml` | `6006` + `4317` | UI+OTLP; default mati (`PHOENIX_ENABLED=false`) |
| `embed` | `docker-compose.embed.yml` | `127.0.0.1:7997` | Infinity BGE-M3 + reranker |
| `qdrant` | profile `qdrant` | `127.0.0.1:6333` | Opt-in; API key wajib |
| `mysql` | profile `mysql` | `127.0.0.1:3307→3306` | Sumber konektor dev/uji; konektor BELUM dipakai jalur produksi (§5.4.5) |

```powershell
cd ..\Infra
docker compose up -d app-db cache          # wajib
docker compose -f docker-compose.embed.yml up -d          # RAG embedding
docker compose --profile qdrant up -d qdrant              # opsional
```

Catatan: `docker-compose.yml` di ROOT repo adalah compose dev/legacy — BUKAN acuan ops utama.

### 6.5 Jalan BE + worker

```powershell
cd ..\ai-app-boilerplate
.\.venv\Scripts\python.exe -m app.scripts.create_user --workspace toko-a --username budi --password '...'
.\.venv\Scripts\python.exe -m app.main                        # prod (reload OFF)
$env:RELOAD='true'; .\.venv\Scripts\python.exe -m app.main    # dev (reload ON)
.\.venv\Scripts\python.exe -m app.workers.ingest              # worker RAG, proses TERPISAH
```

### 6.6 Migrasi DB

```powershell
.\.venv\Scripts\python.exe -m app.infra.rag_migrate   # idempotent; advisory lock anti dobel
```

- Rentang skema di HEAD v2.0.0: **`007–022`** (glob `migrations/*.sql`).
- Pra-deploy: bandingkan `SELECT version FROM schema_migrations` di staging vs daftar `migrations/*.sql`; terapkan berurutan (`docs/runbooks/deploy-rollback.md` §1–3).
- Rollback SKEMA hanya darurat: `migrations/*.rollback.sql` MANUAL berurutan mundur.
- Role: migrasi/reindex/backfill = role BYPASSRLS/migrator; JANGAN sebagai role `app`.

### 6.7 Runbooks — kapan dipakai

| Runbook (`docs/runbooks/`) | Dipakai saat |
|---|---|
| `deploy-rollback.md` | Setiap deploy/rollback: checklist, rolling 1 replika, smoke pasca-deploy |
| `db-rls.md` | Query 0 baris misterius / 403 massal / RLS / reindex `pg=0` |
| `fail-open-cache-down.md` | Alarm `cache_unavailable`, header `X-Degraded: cache` |
| `ingest-stuck.md` | Dokumen stuck `processing` >10 mnt atau `failed`/`dead` massal |
| `llm-down.md` | `llm_errors_total` naik / `stream_error` massal |
| `qdrant-drift.md` | Hasil Qdrant aneh / reindex `match=false`; ukur dulu `--dry-run` |

### 6.8 CI gates + secret-scan

Trigger: push `main` + semua PR (`.github/workflows/ci.yml`).

| Job | Gate |
|---|---|
| `unit` | torch CPU + locks → `pytest -m "not integration"` → `rename_audit --guard` → `skills_lint` |
| `security` | `gitleaks-action` (secret scan) + `pip-audit -r requirements.lock` |
| `build` | `docker build` (assert pin TORCH + digest) → SBOM SPDX → artifact |

Aturan secret (`docs/SECRET-INVENTORY.md`): semua secret = ENV, tak pernah di file/commit; CI gagal bila pola secret muncul di diff; password user = Argon2id.

### 6.9 Troubleshooting Windows

1. **Execution-policy memblokir `setup.ps1`** (`setup.ps1:6-7`): `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.
2. **`SelectorEventLoop` / psycopg menolak Proactor**: selalu jalan via `python -m app.main` (bukan `uvicorn` langsung); tes memasang `WindowsSelectorEventLoopPolicy` eksplisit.
3. **CRLF vs LF** (`.gitattributes`): `*.ps1 eol=crlf`, `*.py eol=lf`. Jangan ubah per fork.
4. **TestFE `npm run build` gagal di sandbox agen**: selalu jalankan di terminal sendiri (sandbox memblokir spawn esbuild, `EPERM`).
5. **Login/CORS/WS 5173**: pastikan `CORS_ORIGINS`/`WS_ORIGINS` memuat `http://localhost:5173`.

## §7 Testing

### 7.1 pytest: unit vs integration

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider -m "not integration"  # unit (CI + setup)
.\.venv\Scripts\python.exe -m pytest tests/ -m integration                                # integrasi (butuh docker)
```

- `pytest.ini`: `asyncio_mode = strict` — test async TANPA marker = GAGAL.
- Marker `integration` = butuh layanan live; skip OTOMATIS bila tak terjangkau.
- Fixture integrasi: `db_url`, `cache`/`cache_url`, `embed_url` (auto-skip), `pool` (`tests/integration/conftest.py`).
- Tiap tes integrasi: workspace unik `it-<hex>` + cleanup best-effort. `claim_job` WAJIB `purge_stale_test_jobs()`.

### 7.2 Pola FakeGraph / FakeCache

- **FakeCache** (`tests/_fake_cache.py`): meniru kontrak `CacheClient` tanpa Redis. Kenapa bukan `fakeredis`: `incr_window` memakai Lua (`EVAL`) yang tak didukung fakeredis.
- **FakeGraph**: meniru subset Runner — `astream_events` v2 + `aget_state` (`tests/test_sse_streaming.py:3`, `tests/test_run_trace.py:3`). Pola: `FakeGraph(script_event, [AIMessage(...)])`, `_HangingGraph` untuk timeout, `interrupts=` untuk HITL.
- Aturan: unit test JANGAN menyentuh layanan live; butuh DB/cache/embed asli = pindah ke `tests/integration/` + marker.

### 7.3 Gate byte-identik + rebase sadar

- `SKILLS_ENABLED=false` → system prompt HARUS byte-identik baseline (hash `sha256[:16]` per agen + `BASELINE_BOUND_TOOLS` + `HITL_DEMO_PROMPT`) — `tests/test_skills_disabled_identical.py`.
- Merah setelah perubahan wiring = perbaiki wiring-nya, JANGAN ganti hash-nya.
- Hash hanya boleh berubah via **rebase sadar**: keputusan eksplisit + bukti diff = tepat 1 blok sisipan (preseden WS2: sisipan `NARRATION_RULES` 554 char; verifikasi strip-1-blok = hash lama v0.8.3; dikunci `test_ws2_rebase_strip_narasi_kembali_hash_lama`).
- Fork: JANGAN edit file gate ini. Merah setelah merge rilis = upstream mengubah prompt → baca CHANGELOG.

### 7.4 Canary / eval

```powershell
.\.venv\Scripts\python.exe -m evals.run --suite rag_answer --tags canary
.\.venv\Scripts\python.exe -m evals.run --all --tags canary
```

- Tier **canary** (tag `canary`): deterministik, tanpa layanan live, gate PR — `pass_rate == 1.0` per agen.
- Tier **live**: nightly, bukan PR.
- 5 suite: `hitl_demo`, `pipeline`, `rag_answer`, `chat_tools`, `rag_chat` (`evals/run.py:19`, `evals/datasets/`).
- CLI (`evals/run.py:107-121`): `--suite/--all`, `--tags`, `--record/--compare BASE.json`, `--min-rate`, `--out`, `--seed 7`; exit 0/1/2. Judge LLM `--judge-rate` default 0.0 = observasi saja (K7); model via `EVAL_JUDGE_MODEL` (`:41`).
- Aturan fork: tiap skill baru = 2 tugas eval (1 positif + 1 parafrasa); target recall fork ≥ 0,90.

### 7.5 Cara tambah tes

1. **Unit** (`tests/test_*.py`): tiru pola FakeCache/FakeGraph §7.2; async wajib marker.
2. **Integrasi** (`tests/integration/test_*_live.py`): marker `integration`; fixture auto-skip; workspace `it-`; `claim_job` → purge; tanpa LLM (kecuali file eval live eksplisit).
3. **Eval**: tambah kasus ke `evals/datasets/<suite>.jsonl` dengan tag `canary`/`live`.
4. **Kontrak beku**: perubahan envelope SSE/WS, RFC7807, skema `/api/v1` = breaking → hanya di mayor berikutnya (`docs/SEMVER.md` §2–3). File kontrak: `test_frozen_contract.py` (beku v0.8.1: format thread, GUC RLS, prefix ontologi, point_id, migrasi), `test_chat_contract.py` (default skema request), `test_contracts.py` (health/livez/urutan SSE/RFC7807/regex thread/job defaults).
5. Verifikasi lokal: unit hijau + `python -m scripts.skills_lint` bersih + `python -m scripts.rename_audit --guard`.

## §8 Fork-per-klien

### 8.1 Clone dari tag + remote upstream vs origin

(Konvensi per `docs/FORK.md:24-35` + `docs/SEMVER.md:27-30`; repo template hanya punya `origin`.)

```powershell
git clone https://github.com/icwicaksana/ai-app-boilerplate.git klien-X
cd klien-X
git checkout v2.0.0                       # mulai dari tag rilis, bukan main mengambang
git remote rename origin upstream         # template = upstream (read-only)
git remote add origin <url-repo-fork-klien>  # fork klien = origin (tulis)
git push -u origin main --tags
```

Prinsip (`docs/FORK.md:3-4`): fork sehat **menambah file, bukan mengedit file inti**; `delta fork` ≈ 0 baris di file template. Tag = annotated, jangan force-push tag yang sudah diambil fork (`docs/SEMVER.md:27-30`).

### 8.2 Yang BOLEH diubah vs JANGAN

Zona v0.9 per `docs/FORK.md` §1–2 + amandemen v2.0.0 (baru: `app/core/run_trace.py`, `docs/TRACE-CONTRACT.md`, blok `NARRATION_RULES` — ketiganya JANGAN):

BOLEH (zona fork):

| Aset | Cara |
|---|---|
| `skills/<nama>/SKILL.md` (+`references/`) | `python -m scripts.new_skill`, tanpa edit kode |
| `app/agents/subagents/<fork>*.py` | `python -m scripts.new_subagent`, 1 file per subagent |
| 1 baris import spec | di composition root MILIK fork |
| `.env` | flag, model, dsb |
| Agen chat fork | copy `TemplateChatAgent.py` → modul baru (jangan edit template) |
| Tool fork | 1 file per tool di `app/agents/tools/` + daftar di ToolRegistry bila global |

JANGAN (zona inti): `app/skills/*`, `app/agents/BaseChatAgent.py`, `app/agents/factory.py`, `app/agents/tools/{SkillTools,DelegationTool}.py`, `app/core/{stream_policy,untrusted,errors,run_trace}.py`, `app/agents/utils/MiddlewareBuilderUtils.py`, `app/schemas/*`, `app/controllers/*`, `docs/TRACE-CONTRACT.md`, `tests/test_skills_disabled_identical.py`.
Butuh perilaku inti berbeda? Ajukan ke upstream dulu — edit inti membuat merge rilis berikut mahal.

### 8.3 Upgrade dari tag template

```powershell
git fetch upstream --tags
git merge v2.1.0        # contoh tag baru; kata FORK.md §3 = merge (rebase histori fork bersama dilarang)
```

Checklist sebelum merge rilis (`docs/FORK.md` §3):

1. `python -m scripts.skills_lint` → BERSIH.
2. Suite unit hijau (`pytest tests/ -m "not integration"`).
3. Gate byte-identik hijau (jangan ubah hash; merah = upstream mengubah prompt → baca CHANGELOG).
4. Eval live + eval fork sendiri; recall/presisi tak turun.
5. `git diff --stat` terhadap tag upstream: file inti = 0 baris berubah.

Pola `docs/UPGRADE.md`: upgrade tanpa mengaktifkan fitur dulu (perilaku identik) → verifikasi `/health` → aktifkan bertahap (skills lalu subagents) → uji.

### 8.4 TestFE: peran + cek cepat

- Peran: harness uji internal Vite+React+TS untuk API `/api/v1` beku — **JANGAN ship ke klien**. 1 tab Session + 11 tab versi + 1 tab Chaos; baca `docs/DESIGN.md` + `docs/PLAN.md` TestFE sebelum mengubah.
- Prasyarat: Node ≥18, BE tag rilis jalan di `http://127.0.0.1:8000`, docker `app-db`+`cache` (+LiteLLM `:4000` opsional), user `toko-a`/`budi`.
- Cek cepat:

```powershell
cd ..\TestFE
npm install
npm run dev         # http://localhost:5173 — terminal sendiri
npm run typecheck   # tsc strict — gate utama
npm run build       # WAJIB terminal sendiri (sandbox agen memblokir esbuild)
```

- Kontrak: `npm run check:contracts` melawan `/openapi.json` (nyalakan `ENABLE_DOCS=true` sesaat → matikan lagi).
- Kompatibilitas: `src/compat.ts` baca `/health.version` vs `config.beMatrix`; matriks v2.0.0 = `["v2.0.0"]` (`src/config.ts:18`) — v1.0 di-drop karena FE baru tak baca event `token`.
- Larangan: DILARANG ubah backend dari repo TestFE; DILARANG secret backend di `.env`/kode.
