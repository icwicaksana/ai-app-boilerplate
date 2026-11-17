# Changelog Boilerplate

Semua perubahan template dicatat di sini. Rilis = git tag (`v0`, `v0.1`, ...).

## v0.9.0 (2026-11-17) — Skills on-demand + Subagents

**Ringkasan.** Prosedur pindah dari system prompt ke file skill yang dimuat
sesuai kebutuhan (hemat ~53% token prompt pada 6 skill), dan tugas riset
berat bisa didelegasikan ke subagent berkonteks terpisah dengan kontrak
Brief/Report. Kedua flag default **mati** — perilaku saat mati byte-identik
v0.8.3 (dikunci snapshot hash). Tanpa migrasi DB, tanpa event SSE baru.

**Skills.** Format `skills/<nama>/SKILL.md` + frontmatter
`name/description/when_to_use/tags` (E01–E08, `SKILLS_STRICT` gagalkan boot
bila rusak). Indeks 1-baris di akhir prompt (prefix-cache aman); tool
`load_skill`/`read_skill_file` factory-bound ke registry boot; body di-quoting
sebagai DATA (`app/core/untrusted.py`); dikecualikan dari pembersihan konteks
+ limit 10 (dulu 5). Lint: `python -m scripts.skills_lint`.

**Subagents.** Roster statis di kode (`SUBAGENTS`); LLM hanya pilih kapan/
siapa/Brief. `make_subagent_tool()` satu-satunya jalur sah: tag `subagent`,
`disable_streaming`, rem tool-call, tolak tool approval-gated dan rekursi
saat build (LOUD). Retry 3× transien + circuit breaker proses-lifetime;
timeout 10s/attempt, 45s/run. Report `ok/partial/failed/timeout` — gagal
tak boleh terlihat sukses. Nama tool `subagent__<nama>` (titik dua ditolak
API OpenAI). Contoh hidup: `demo-stok`.

**Keamanan & ketahanan.** `StreamPolicy` (filter event by tag, fail-closed) +
regresi kebocoran token pada graph nyata. Factory agent tunggal menutup
`bound_tools=[]` diam-diam di resume/fork (termasuk sumbu memori).
`durability="sync"` jalur chat. Detail error 500 digenerikkan ke klien
(`public_error_detail`). Health `checks.skills` fail-open.

**Eval live** (`tests/integration/test_eval_skills_live.py`, 6 skill,
deepseek-v4.1-flash): recall=1.00, presisi=1.00, 0 load di 18 adversarial,
savings=0.53, keyword=1.00.

**Docs.** `docs/SKILLS-V09.md` (operator), `docs/UPGRADE.md` (naik versi +
rollback), `docs/FORK.md` (zona fork vs inti), `docs/V09-PLAN.md` (desain +
keputusan K1–K12).

**Upgrade.** Tarik tag, install (`PyYAML` baru), deploy — perilaku identik
sampai flag dinyalakan. Rollback = deploy tag lama (tanpa state tersisa).

> **Glosarium istilah (sejak v0.8.1).** Entri historis v0.3–v0.8 memakai
> istilah **`tenant`**. Sejak v0.8.1 istilah itu diganti **`workspace`**
> dengan semantik sama. Alasan: boilerplate ini **di-fork per klien**
> (1 deployment = 1 klien), sehingga kata "tenant" menyiratkan model
> "satu BE melayani banyak penyewa" yang tidak dipakai. Sebagian nilai
> tersimpan (GUC RLS `app.current_tenant`, namespace memori, prefix
> ontologi `tenant:`, payload Qdrant) sengaja **dibekukan** sampai v0.8.2
> karena butuh migrasi data — lihat `Docs/RENAME-V081.md`.

## v0.8.3 (2026-11-16) — Contract-shrink: cabut jalur kompatibilitas

**Konteks keputusan.** v0.8.2 menambahkan dual-read (klaim JWT, prefix
ontologi, GUC RLS) dengan asumsi ada deployment berjalan yang tokennya terbit
sebelum rename. Asumsi itu **salah**: template ini belum punya fork yang
berjalan, jadi tidak ada token/data lama yang perlu dilayani. Jalur
kompatibilitas yang tidak melayani apa pun hanya menambah permukaan yang bisa
disalahgunakan — jadi dicabut.

**JWT.** `decode_token` kembali menuntut klaim `workspace` lewat
`options={"require": [...]}` (deklaratif, bukan cek manual). `legacy_claim`,
`LEGACY_CLAIM_WORKSPACE`, `LEGACY_CLAIM_SUNSET`, dan date-bomb
`tests/test_legacy_claim_sunset.py` dihapus. Konsekuensi yang diterima:
token terbitan pra-v0.8.2 kini **401** — tidak ada yang terdampak.

**Ontologi.** `LEGACY_EXT_PREFIXES`/`EXT_PREFIXES` dan `canonical_extension()`
dihapus; `extension_suffix()` hanya mengenali `workspace:`. Data lama sudah
dipindahkan migrasi 018 (KG kosong saat itu), jadi tidak ada yang jadi yatim.

**RLS (migrasi 020).** Policy berhenti memakai
`COALESCE(NULLIF(ws,''), NULLIF(tenant,''))` dan membaca **hanya**
`current_setting('app.current_workspace', true)`. Fail-closed tetap terjaga:
GUC tak diset → NULL → `workspace = NULL` = NULL (bukan TRUE) → 0 baris; GUC
`''` → 0 baris. `ALTER POLICY` dipakai supaya tidak ada jendela RLS tanpa
policy; preflight menolak drift (policy ≠ 8).

**Teks user-facing.** Pesan error dan skema API yang masih menulis format
thread `tenant:user:session` diperbarui ke `workspace:user:session`. Format
kawatnya **tidak berubah** (tetap 3 segmen `a:b:c`, ADR-07).

**Guard.** `FROZEN_ALLOWLIST` menyusut dari 10 entri menjadi **1**: nama
berkas `014_rename_tenant_to_workspace.sql` pada daftar `MIGRATIONS` — satu-
satunya yang tidak boleh di-rename karena versinya tercatat di
`schema_migrations`. Di `app/` kini tersisa **tepat satu** kemunculan istilah
lama, yaitu nama berkas itu.

**Gate.** 709 unit hijau; guard exit 0; migrasi 007–020 tercatat; 8 policy
`workspace_isolation` tanpa cabang GUC lama.



## v0.8.2 (2026-11-16) — Selesaikan Tier C: GUC, namespace, ontologi, kode error

**Perbaikan regresi v0.8.1 (P1).** v0.8.1 mengganti klaim JWT `tenant` →
`workspace` **termasuk** di `options={"require": [...]}` tanpa fase transisi,
sehingga token yang terbit sebelum deploy langsung ditolak 401. Kini
**dual-read**: `workspace` diutamakan, nama lama diterima sebagai fallback;
`TokenClaims.legacy_claim` untuk telemetri. `tests/test_legacy_claim_sunset.py`
adalah **date-bomb**: gagal setelah 2026-11-30 dan memaksa pencabutan.

**GUC RLS (P3, migrasi 016).** 8 policy → `workspace_isolation`, ekspresi
`COALESCE(NULLIF(current_setting('app.current_workspace', true), ''),
NULLIF(current_setting('app.current_tenant', true), ''))`.
Kenapa COALESCE, bukan switch langsung: `current_setting(..., true)`
mengembalikan **NULL, bukan error** — policy yang hanya membaca GUC baru pada
sesi yang menyetel GUC lama mengevaluasi `workspace = NULL` → **0 baris tanpa
satu pun exception** (SELECT 0 baris, DELETE 0 baris, INSERT ditolak).
COALESCE membuat urutan deploy apa pun aman dan tetap fail-closed.
`NULLIF` wajib: GUC yang di-set `''` bernilai `''`, bukan NULL.

**OUT param fungsi (migrasi 017).** `claim_ingest_job` `job_tenant` →
`job_workspace`. `CREATE OR REPLACE` mustahil (nama OUT = bagian tipe
kembalian) → DROP+CREATE dalam satu transaksi, dengan re-assert OWNER,
`REVOKE ALL FROM PUBLIC`, `GRANT` ulang, dan `SET search_path = public`.

**Migrasi data (migrasi 018).** `store.prefix` `tenants.<t>.users.<u>` →
`workspaces.<t>.users.<u>` (separator **titik**, bukan slash — nilai on-disk
LangGraph; pola slash akan mengubah 0 baris secara senyap) dan prefix
ontologi `tenant:` → `workspace:` di `kg_entities.type`/`kg_edges.relation`.
`ONTO_VERSION` **tidak** di-bump: ia dipakai sebagai filter baca, jadi bump =
KG blackout sampai reindex penuh.

**Keamanan (migrasi 019).** Temuan baru: PostgreSQL memberi `EXECUTE` kepada
`PUBLIC` secara default pada setiap fungsi baru, dan 007/014 hanya menulis
`GRANT ... TO app_api` tanpa `REVOKE ... FROM PUBLIC`. Akibatnya role DB mana
pun bisa memanggil `get_source_secret(uuid)` (milik `rag_admin` BYPASSRLS) dan
membaca `secret_enc` **semua** workspace. Kini dicabut dari PUBLIC, diberikan
hanya ke `app_api` + role koneksi.

**Kode (P2, P4).** Kode error `CROSS_TENANT_DENIED` →
`CROSS_WORKSPACE_DENIED`. Ontologi: prefix kanonis `workspace:`,
`LEGACY_EXT_PREFIXES` untuk baca data lama, `canonical_extension()` menormalkan
tulisan (mencegah duplikat `tenant:x` vs `workspace:x`), `verbalize` merender
keduanya identik sehingga `gpath_id` sitasi stabil.

**Gate.** 715 unit hijau; 19 integrasi hijau (termasuk kontrak retrieval dua
backend + persistensi profil dengan namespace baru); guard CI exit 0 dengan
allowlist menyempit ke **satu baris definisi per modul**; dua jangkar beku
dipensiunkan sesuai aturan berkasnya (tanggal + alasan) dan diganti jangkar
setara.



## v0.8.1 (2026-11-16) — Rename `tenant` → `workspace` (tanpa perubahan perilaku)

**Terminologi (F1–F3).** 1.806 occurrence di 121 file → `workspace`:
identifier Python, prosa, docstring, pesan error, log, `.env.example`,
README. Format thread `tenant:user:session` **tidak berubah** (regex `a:b:c`
beku, ADR-07) — hanya nama konsepnya.

**Skema (F4).** Migrasi `014_rename_tenant_to_workspace.sql`: kolom di 11
tabel, constraint UNIQUE, index, 8 policy RLS (USING + WITH CHECK), body
`claim_ingest_job`. GUC `app.current_tenant` **sengaja dibekukan** (mengganti
GUC butuh pola dual-set dua fase; `current_setting(..., true)` gagal
diam-diam = 0 baris, bukan error).

**Keamanan — temuan gate F4.** Migrasi `015_fix_secret_enc_grant.sql`
menutup kebocoran nyata sejak v0.7: `app_api` bisa `SELECT secret_enc`
meski 007 menulis `REVOKE ALL (secret_enc)`. Sebabnya PostgreSQL tidak punya
ACL negatif — `GRANT ... ON tabel` lalu `REVOKE ... (kolom)` tidak mencabut
apa pun. Perbaikan: cabut grant tabel, beri ulang per kolom tanpa
`secret_enc` untuk SELECT (write-only secret; baca hanya via
`get_source_secret()` yang audited).

**Auth (F5).** Mode `trusted_headers` (v0.3: identitas dari header
`X-Tenant-ID`/`X-User-ID`) **DIHAPUS TOTAL** — header bisa dipalsukan.
`AUTH_MODE` kini hanya `jwt`; `AUTH_ALLOW_TRUSTED_HEADERS` hilang dari
Settings; header legacy dikeluarkan dari CORS.

**Gate.** 709 unit hijau; `tests/test_frozen_contract.py` (15 assert) hijau
sebelum dan sesudah tanpa diedit; `scripts/rename_audit.py --guard` menolak
istilah lama masuk kembali ke `app/`. Migrasi 014 diuji: kegagalan =
seluruh transaksi rollback, skema utuh.

**Dibekukan ke v0.8.2 (butuh migrasi data + dual-read).** Namespace memori
`"tenants"`, prefix ontologi `"tenant:"`, payload Qdrant `key="tenant"`,
klaim JWT `tenant`, GUC `app.current_tenant`, kode error
`CROSS_TENANT_DENIED`, OUT param `job_tenant`.

## v0.8 (2026-11-16) — Knowledge Graph PG-native (LightRAG-can)

**Arsitektur (F1–F2).** Graph PG-native 2 tabel (`kg_entities`,
`kg_edges`, migrasi 011) + alias (013) + RLS WITH CHECK (012):
BFS bounded iteratif (BUKAN recursive CTE), factory-bound tenant (D2),
filter `access_level` kolom (D13), stage worker `graph(95)` + anti-stale,
hapus dua-sisi, health `graph`. Protokol `GraphStore`/`EntityExtractor`.

**Retrieval (F3).** Seed prioritas + preseed alias + confidence
bobot-tier → BFS adaptif (cap 2/6, hop-2 bila kurus) → RRF 3-way
(2-way identik bila graph mati) → floor ganda (natural 0.05, graph
0.10 kalibrasi) → sitasi `(+N bukti)` + grounding G1–G6 (provenance
struktural, union angka, singleton-lemah → refusal).

**Ekstraktor (F4).** L1 aturan-ID (span intra-kalimat) + L2 GLiNER2.5
(CPU, threshold 0.3 + filter ko-okurensi kalimat) + L3 LLM budgeted
(trigger ketat, cache/breaker/kuota; default MATI). Spike: cold 16s,
warm ~530ms/teks, trigger 2/12. Dep: `gliner2==2.1.0`.

**Eval (F5).** Gold-graph 28 QA + praregistrasi + 6 sel ablation × 2
backend. Run 7 resmi: FULL 27/28 dua backend (B0 16/28), bridge 8/8,
alias 4/4, FPR=0, graph 14–31ms (budget 800ms). Kill K1–K4 tak terpicu.

## v0.7.1 (2026-11-13) — Retrieval robustness (ukur-dulu, Opsi B)

**Observabilitas (F1).** `RetrievalTrace` per-request: timing
embed/dense/lexical/rerank/total + counts + top_score + floor + status +
query_preview. Diekspos di meta `/retrieval/search`, `output.data`
`rag_answer`, dan log INFO satu-baris. Tanpa dep baru.

**Gold-30 (F2).** Runner `tests/integration/test_gold30_live.py`: 10
stratum × 30 QA (faktoid/parafrase/morfologi/angka/tabel/negasi/typo/
multi/cross/noanswer) di pgvector + qdrant. Satu-satunya gate = canary
faktoid 5/5; sisanya diukur (tabel + JSON `Test/spike_v07/gold30_*.json`).

**normalize.py + migrasi 010 (F3).** Fold + ekspansi angka-skala idempoten
+ stopword tanya/fungsi + stem Sastrawi (opt-out, `RAG_STEMMER`, wajib
sama index+query). Sisi lexical saja (dense/rerank mentah). Temuan F3:
kolom GENERATED `coalesce('')` tak pernah NULL → query memakai OR dua
cabang (norm ATAU mentah, rank GREATEST) — nol regresi, tanpa backfill.
Qdrant: payload + index `content_norm` (+best-effort koleksi lama).
Dep: `sastrawi==1.0.1`.

**Gerbang CRAG: TERTUTUP (F4).** Failure-rate 2/30 = 6,7% < 15% di
KEDUA backend (miss sama: sinonim "komputer jinjing"↔laptop + jebakan
AND "35 ribu"). Floor 0.05 tervalidasi (min-hit 0.066, no-evidence 6/6).
CRAG tidak dibangun (sesuai ADR-22); 2 miss = ekspansi-kueri/OR-lexical
(defer ke v0.8).

**Fallback embed model-identik (F5b).** `FallbackEmbeddings`: failover
per-request + recovery otomatis + flag `on_fallback` di `/health`.
Factory tolak start bila nama/dims beda (D6); fallback down saat start
= warning + jalan normal. Chaos live hijau (primer port-mati → 1024-dim
nyata). Rerank tetap degradasi RRF (tanpa fallback terpisah).

**Testing (final): 633 unit + 92 integrasi + 1 skip, 0 gagal** (incl. contract
18/18 pasca-010, gold30 28/30 ×2 backend, chaos fallback, E2E v0.7 29/29
regresi-hijau).

## v0.7 (2026-11-13) — RAG murni (dual backend, CPU-only)

**Retrieval dual backend di balik 1 protocol (ADR-19).** `EmbeddingsPort` /
`RerankerPort` / `VectorStorePort` + `HybridRetriever` orkestrasi tunggal +
`BoundRetriever` (tenant factory-bound dari JWT — tanpa argumen tenant di
`search()`). Implementasi: pgvector (default, HNSW + tsvector + RLS, di
`app-db`) dan Qdrant (opt-in, compose profile, int8 + filter wajib). Ganti via
`RAG_BACKEND` tanpa ubah kode agen. Hybrid: dense50 + lexical50 → RRF(60) →
20 kandidat → rerank (query128/doc384/pair512, batas kalimat) → 5 final +
`rerank_score_floor=0.05` (kalibrasi empiris: terkait 0.29–0.99 vs tak-terkait
≤0.0001; chunk bawah-floor dibuang dari konteks) → `retrieval_status`
(`ok`/`no_evidence`).

**Dedicated infra embedding CPU (ADR-20).** `Infra/docker-compose.embed.yml`:
Infinity v2 CPU + BGE-M3 + bge-reranker-v2-m3 (INT8 ONNX, API OpenAI-compatible,
dipakai banyak app). Fallback beda-model DILARANG (down = 503 eksplisit);
tokenizer satuan BGE-M3 via cache repo-lokal `.hf-cache` (bukan `~/.cache`).

**Ingest worker durable (ADR-21).** `python -m app.workers.ingest` (proses
terpisah, pool sendiri, Postgres-queue + lease `SKIP LOCKED` + heartbeat +
backoff+jitter + DLQ) + endpoint `/documents/*` (202, dedup sha256,
Idempotency-Key, rate-limit ingest, 415 sniff-MIME, tolak macro) + SSE progress
skema sendiri (SoR Postgres) + `POST /retrieval/search` (debug) +
reconciler `python -m app.workers.reindex --tenant --since --dry-run`.

**Parser modular + OCR opt-in + XLSX.** Docling (PDF digital/DOCX/PPTX, prov
halaman + span lintas-halaman), Markdown header-aware + tabel pipe, XLSX
read-only (guard sel + tolak VBA), probe pypdf murah, OCR Tesseract terpisah
(`OCREngine`, `ind+eng`, DPI-300, confidence, `low_ocr`): `auto` = tawarkan
`OCR_REQUIRED`, `on` = kerjakan. Preset chunk beku `prose/markdown/pdf/factoid`
+ baris-tabel selalu difactoid-kan.

**SourceConnector read-only (ADR-23).** Protocol tanpa method tulis +
Postgres & MySQL (watermark `(updated_at,id)`, soft-delete, allowlist tabel,
proyeksi kolom, transaksi READ ONLY); secret Fernet per tenant
(`SOURCE_CONN_KEY`, generate otomatis di setup).

**Agen RAG + grounding.** `rag_answer` (pipeline: retrieve → generate →
gerbang ketat; `no_evidence` = tolak tanpa panggil LLM; angka tanpa sitasi /
marker yatim → refusal eksak) + `rag_chat` (ReAct + tool factory-bound,
reformulasi coreference di prompt, flag `grounded`). Sitasi `[^n]` + `chunk_id`
stabil sha256 + chunk ter-quoting (anti-injeksi). Middleware P7: retrieval
limit 10 + exclude dari `ClearToolUsesEdit`. Tool retrieval WAJIB
factory-bound (M6 — contoh Docs tak-aman diperbaiki).

**Eval mini Indonesia.** 8 gold QA (5 hit@3 + 3 no_evidence incl. isolasi
silang A↔B) + jawaban LLM nyata (grounded + sitasi valid + refusal eksak).

**Testing (final): 617 unit + 89 integrasi + 1 skip, 0 gagal.**
Contract suite ganda 18/18 (pgvector+Qdrant); HTTP 17; live API/worker/eval/
konektor (PG+MySQL); `Test/spike_v07/live_e2e_v07.py` **29 pemeriksaan,
2 fase backend, semuanya HIJAU** — upload MD/PDF/XLSX, SSE, dedup, 415,
OCR_REQUIRED→retry, search, rag_answer grounded+refusal, rag_chat
silang-tenant, hapus, edge 410/404, fase Qdrant penuh. Smoke test image:
container `healthy`, `/livez`+`/health` 200 (embed/rerank/vector/cache/db
semua ok), impor worker OK.

**Temuan saat verifikasi image/E2E (diperbaiki):**
- **Image 11.2 GB (torch CUDA)** → install `torch==2.15.0` dari index CPU
  resmi DULU (`2.15.0+cpu` ±200 MB, memenuhi pin lock) → image **3.7 GB**,
  tanpa lib `nvidia-*` yang tak terpakai di server CPU-only.
- **`migrations/` tak disalin Dockerfile** → container akan gagal start
  ("007_rag.sql tidak ditemukan"). Kini `COPY migrations` + guard test.
- **`.dockerignore` tidak ada** → build context 2.1 GB (`.venv` 1.6 GB +
  `.hf-cache` 0.5 GB) untuk kode 1.2 MB; transfer lambat + daemon Docker
  wedge saat unpack. Kini context ≈ kode + lock.
- **Cold start Infinity** (muat model 30–60 dtk) membuat `init_rag` gagal →
  worker/API mati. Kini probe di-retry ~2 menit dengan pesan aksi jelas.
- **aiomysql menolak `read_timeout`/`write_timeout`** (TypeError) → konektor
  MySQL crash saat connect; kini hanya `connect_timeout`.
- **Queue worker global vs test**: job basi run yang dibatalkan membuat
  assertion claim rapuh → helper `purge_stale_test_jobs` (hanya tenant test).
- Image kini non-root (`appuser`), direktori runtime `/data/uploads` +
  `/cache/hf` disiapkan (aman untuk volume kosong).
- Migrasi `007_rag.sql` + `008_rag_grants.sql` + `009_rag_lease_fix.sql`
  (state≠stage, fencing `claimed_by`, batas attempts, sweep poison-pill).

**Deviasi sadar:** Qdrant lexical = boolean `MatchText` (tanpa BM25
berperingkat); SSE progress = poll Postgres (ekor Valkey dipublish worker,
dibaca nanti); MySQL = profil compose opt-in; eval = 8 gold QA fokus
(30-QA → v1.0 `evals/`).

## v0.6.1 (2026-11-01) — Batas umur koneksi WS + E2E menyeluruh

**Batas umur koneksi WebSocket (`WS_MAX_CONNECTION_SECONDS`, default 1800).**
Handshake hanya membuktikan identitas **sekali**; koneksi panjang bisa hidup
lebih lama dari access token. Sekarang:
- setelah `accept`, server mengirim frame `ready` berisi `expires_in` +
  `heartbeat` sehingga klien bisa menjadwalkan re-ticket **sebelum** diputus;
- saat batas tercapai: frame `auth_expired` (`reason`, `close_code`) lalu
  close **4001**; klien minta ticket baru, reconnect, lalu `resume` turn
  (v0.5a) tanpa kehilangan token jawaban.

**Agent demo HITL `hitl_demo`** (tanpa LLM): graf dua node
(`propose` → `interrupt` → `execute`) dengan payload refund Rp 750.000.
Dipakai untuk mencoba approval end-to-end dan untuk E2E lintas proses tanpa
API key LLM.

**Bug ditemukan lewat E2E & diperbaiki:** jalur turn-log menulis **dua**
event `started` (dari route + dari runner). Kini `TurnManager` melewati
`started` dari producer sehingga kontrak "started tepat satu kali" utuh.

**Testing (naik dari 524 → 545):**
- `test_ws_live.py` diperluas: `ready` frame, ticket sekali pakai, ticket
  dicabut, Origin asing/allowlist, envelope+`seq` (ready=seq 1), `BUSY` saat
  run aktif, `cancel`, pesan ngawur, error validasi sebagai frame (bukan
  disconnect), dan **batas umur koneksi** (auth_expired + close 4001).
- `test_hitl_live.py` diperluas: approval kedaluwarsa → 409 `APPROVAL_EXPIRED`,
  id tak dikenal → 404, decision invalid → 422, reject-lalu-approve → 409,
  **tamper audit → `verify_chain` gagal** dengan `first_bad_id`, dan tidak ada
  pending tersisa setelah approve.
- `Test/spike_v06/live_e2e.py` — **E2E terhadap uvicorn nyata (Postgres +
  Valkey)**: 21 pemeriksaan hijau, mencakup SSE `pending_approval` + `X-Turn-ID`,
  resume replay + `Last-Event-ID`, turn asing → 410, WS `ready`/ping-pong/
  envelope, ticket reuse & Origin ditolak, approval tercatat → approve →
  resume `completed`, double-decide 409, audit chain valid.

## v0.6 (2026-11-01) — HITL tool-level + approval API + audit hash-chain

**Deteksi interrupt** (dari v0.4.1) kini dipakai penuh: `snapshot.interrupts`
→ `interrupts: [{id, value, response_schema}]` + `status=pending_approval`.

**Policy (`app/core/hitl.py`, ADR-22).** `HITL_POLICY` per tool:
`amount_field` + `threshold` (default: `process_refund` > Rp 500.000),
`allowed_decisions`, `expires_in_seconds`, dan `policy_version` (hash policy
untuk audit/TOCTOU). Middleware resmi `HumanInTheLoopMiddleware` dipasang
dengan `InterruptOnConfig(when=...)` — threshold dievaluasi dari argumen tool.

**Tool demo** `process_refund` (stub deterministik, tanpa uang nyata) agar
alur approval bisa diuji end-to-end.

**Penyimpanan (`app/infra/approvals.py`, ADR-23/25).**
- `approvals`: satu baris per interrupt, UNIQUE `(thread_id, interrupt_id)` →
  resume idempoten; status `pending/approved/rejected/expired`.
- `audit_log`: **append-only + hash chain** (`prev_hash`+`hash` = SHA-256 isi
  kanonis), ditulis dalam transaksi dengan advisory lock per tenant;
  `verify_chain()` mendeteksi penyuntingan/penghapusan.
- Semua query parameterized.

**API (`/approvals`, `/audit`).** `GET /approvals?status=pending`,
`GET /approvals/{id}`, `POST /approvals/{id}/decide` (`approve|reject` +
alasan), `GET /audit`, `GET /audit/verify`. Keputusan **memicu resume graph**
(`Command(resume={"decisions": [...]})`), bukan hanya menandai baris.

**Anti double-approve + TOCTOU.** Klaim keputusan atomik
(`UPDATE ... WHERE status='pending' AND expires_at > now() RETURNING`) → klik
kedua dapat 409. Sebelum eksekusi, `action_hash` dihitung ulang dari interrupt
yang masih hidup di checkpoint; bila berbeda → `409 APPROVAL_STALE` (kelas
serangan CVE-2026-53822: argumen berubah setelah approval).

**Expiry.** `expires_at` per policy; `expire_stale()` dipanggil saat list/get;
approval kedaluwarsa → `409 APPROVAL_EXPIRED`.

**Hook streaming.** Event `result` berstatus `pending_approval` (SSE maupun WS)
otomatis mencatat baris approval + audit `approval_requested` (best-effort:
kegagalan DB tidak memutus stream).

**State API (aditif).** `GET /threads/{id}/state` kini menyertakan `next`
(node berikutnya) dan `interrupt_ids` (untuk dashboard approval).

**Tests:** 484 unit + 43 integrasi. Baru: `test_hitl_policy.py` (threshold,
hash aksi, hash chain), integrasi `test_hitl_live.py` (graf NYATA yang
interrupt: record → approve → resume → selesai, reject tidak resume, stale
409, isolasi tenant, `verify_chain`).

## v0.5 (2026-11-01) — Realtime: resumable SSE (turn-level) + WebSocket

**v0.5a — Resume turn-level (ADR-20).** Streaming SSE sekarang **tahan putus**:
- `app/infra/turnlog.py`: satu turn = satu Valkey Stream (`turn:{tenant}:{turn_id}`).
  Event pertama + TTL ditulis **atomik** (Lua XADD+EXPIRE) — key tetap punya TTL
  walau proses mati tepat setelah tulis. Event terminal (`result`/`error`)
  mengakhiri; `finish()` memperpendek TTL ke jendela reconnect; kedaluwarsa → 410.
- `app/core/turns.py`: **producer terpisah dari koneksi klien** (task latar).
  Disconnect klien TIDAK menghentikan produksi; klien lain bisa menyusul.
- SSE `id:` = entry ID stream (satu-satunya cursor); `Last-Event-ID` melanjutkan
  dari posisi terakhir. `started` membawa `turn_id` (aditif).
- `GET /chat/stream/{turn_id}` → replay + live sampai terminal; 410 `STREAM_GONE`
  bila kedaluwarsa/tidak dikenal.
- Tanpa cache (`CACHE_URL` kosong) → streaming langsung seperti v0.4 (degradasi
  eksplisit, didokumentasikan).

**v0.5b — WebSocket `/ws/chat` (ADR-21).**
- Auth **ticket sekali pakai**: `POST /realtime/ticket` (Bearer) → ticket TTL 30 dtk,
  di-consume atomik (GET+DEL). Browser tak bisa kirim header `Authorization` dan
  token di query bocor ke log — ticket menutup keduanya.
- **Origin allowlist** (CWE-1385 / OWASP ASVS 4.4): origin asing ditolak 1008.
- Envelope identik SSE + `seq` monotonic per koneksi; ping/pong; `cancel`;
  `resume` memakai turn log yang sama dengan SSE.
- **Backpressure**: queue per koneksi `WS_MAX_QUEUE`; frame `token`/`thinking`
  di-drop saat penuh (aman — bisa resume), frame terminal tidak pernah di-drop;
  klien yang terus lambat diputus 1013.
- Heartbeat aplikasi `WS_HEARTBEAT_SECONDS` + presence `ws:presence:{tenant}:{user}`
  (TTL 2× heartbeat, dihapus saat disconnect).
- Shutdown: producer turn dibatalkan, cache ditutup bersih.

**Tests:** 512 hijau (unit + integrasi). Baru: `test_turnlog.py` (producer,
terminal, cancel), `test_ws_ticket.py` (single-use, Origin), integrasi
`test_turnlog_live.py` (Streams nyata: cursor, TTL, isolasi tenant),
`test_resume_live.py` (putus → reconnect → `Last-Event-ID` tanpa gap),
`test_ws_live.py` (ticket, Origin, envelope+seq, cancel, pesan ngawur).

## v0.4.1 (2026-11-01) — Hotfix deteksi interrupt LangGraph

**Bug (latent sejak v0.3.1):** jalur streaming (`Runner._final_result_event`)
membaca `aget_state().values["__interrupt__"]` — field itu **tidak pernah ada**
di `values`. Interrupt hidup di `StateSnapshot.interrupts`. Akibatnya chat/stream
SELALU melaporkan `completed` walau graf berhenti menunggu approval; klaim "B5"
v0.3.1 hanya lolos karena test memakai fake graph.

**Perbaikan:**
- `Runner` membaca `snapshot.interrupts` (bentuk nyata LangGraph) + helper
  `normalize_interrupts()` → `[{id, value, response_schema}]`.
- Kontrak wire **aditif**: `StreamResult.interrupts` (list) di samping
  `interrupt` (nilai pertama) — klien bisa menjawab per `id` (resume map).
- `Runner.resume()` menerima nilai tunggal **atau** map `{interrupt_id: value}`
  dan meneruskan status/interrupt hasil resume.
- `GRAPH_DURABILITY` (`sync|async|exit`): checkpoint "sync" tersedia untuk alur
  HITL agar pause tak hilang saat crash; default tetap `async`.
- `Runner._ainvoke()` fallback bila graph kustom tak menerima `durability`.

**Tests:** `tests/test_interrupt_detection.py` — graf LangGraph **asli**
(StateGraph + InMemorySaver): run/stream mendeteksi interrupt, resume via map
id, run tanpa interrupt bersih, normalisasi bentuk. 452 unit hijau.

## v0.4 (2026-11-01) — Cache (Valkey) + Auth JWT + hardening

**BREAKING (auth):** identitas TIDAK lagi dari header `X-Tenant-ID`/`X-User-ID`.
Default `AUTH_MODE=jwt`; route chat/jobs/threads butuh `Authorization: Bearer
<access_token>`. Header lama diabaikan (bukan fallback diam-diam). Migrasi:
README §Migrasi v0.3.1 → v0.4.

**Infra (ADR-13):** service `cache` = `valkey/valkey-bundle:8.1.11-alpine`
(pin exact), host `6380`, tanpa volume (ephemeral by design), password
`CACHE_PASSWORD`. Modul `json`/`search`/`bloom` tersedia untuk kebutuhan nanti
(RAG v0.7) walau v0.4 hanya memakai perintah inti.

**Cache layer (`app/infra/cache.py`):** satu pool di lifespan;
`CacheUnavailable` seragam; key ter-namespace (`rl:*`, `idem:*`, `jwt:deny:*`);
Lua atomik untuk counter window. Tanpa fallback in-memory (ADR-15). Startup
fail-fast bila `CACHE_URL` diisi tapi tak terjangkau; runtime fail-open (ADR-16).

**Auth JWT (`core/security.py`, `core/auth.py`, `routes/auth.py`):**
Argon2id (`pwdlib`); **PyJWT HS256 dipin** (alg-confusion ditolak), klaim wajib
`exp/iat/jti/sub/typ/tenant/iss`. Endpoint: `POST /auth/login`,
`POST /auth/refresh` (**rotasi**: refresh lama masuk denylist sebelum yang baru
terbit), `POST /auth/logout` (revoke access+refresh), `GET /auth/me`. Tabel
`app_users` di app-db + CLI `python -m app.scripts.create_user`. Pesan gagal
login SELALU generik (anti-enumerasi). `get_thread_identity` jadi satu titik
tukar — controller/`ensure_owner` tak berubah, 403/404 tetap seperti v0.3.

**Rate limit (`core/ratelimit.py`):** fixed-window per (tenant,user) via Lua;
`/chat/stream` & `/jobs/*` + `/auth/login` per (IP+username). 429 RFC 7807 +
`Retry-After` + `X-RateLimit-Limit`; fail-open saat cache down (terlihat di
`/health` → `cache: down` + log warning; status juga dicatat di
`request.state.rate_limit_degraded`).

**Idempotency (`core/idempotency.py`):** `Idempotency-Key` untuk `POST /jobs/*`
→ replay tanpa eksekusi (`Idempotent-Replayed: true`), payload berbeda = 409
`IDEMPOTENCY_KEY_REUSED`, paralel = 409 `IDEMPOTENCY_IN_PROGRESS`, eksekusi
gagal melepas kunci (boleh retry).

**Hardening:** `SecurityHeadersMiddleware` (nosniff, DENY frame, referrer,
permissions, COOP/CORP; HSTS hanya prod); `/docs`+`/openapi.json` mati default
(`ENABLE_DOCS`), prod mewajibkan mati; batas payload jobs
`MAX_JOB_PAYLOAD_BYTES` → 413; guard config prod (jwt+cache+no-docs, tolak
trusted-headers); Phoenix dimatikan otomatis bila collector tak terjangkau
(mencegah request lambat akibat retry OTLP).

**Proxy (ADR-18):** `litellm_settings.cache` exact-match via Valkey. Semantic
cache SENGAJA belum (butuh valkey-search + embedding; berisiko untuk trafik
agenik multi-turn) — kandidat v0.4.x/v0.7.

**Tests:** 444 unit + 21 integrasi (Valkey+Postgres live) = 465 hijau, plus E2E
17 pemeriksaan terhadap server berjalan (`Test/spike_v04/live_v04.py`):
login/401/403/404, rate-limit 429+Retry-After, rotasi refresh, logout revoke,
security headers. Bukti Valkey: `rl:*`, `jwt:deny:*`, TTL aktif.

**Diketahui (bukan bug):** gateway down → 502 setelah ±8 dtk karena retry SDK
OpenAI di dalam LiteLLM (bukan retry BE); turunkan `num_retries` klien bila
perlu.

## v0.3.1 (2026-10-26) — Remediasi review v0–v0.3 (memory, SSE, request-id, config)

Hasil review 5-reviewer + verifikasi silang (laporan final: nol bug correctness
fatal di logika inti; temuan di keamanan memori, observability, validasi
boundary, dan lubang test). Tanpa bongkar arsitektur, tanpa ubah kontrak beku.

**Keamanan memori (CRITICAL B1 + 10 MAJOR):**
- Fakta TAK LAGI digabung ke SystemMessage — disuntik sebagai HumanMessage
  kedua (id stabil `user-facts-v1`) ter-quoting + label anti-injeksi; nilai
  dinetralkan dari spoof label/newline; kap per-bagian di batas kata.
- Jaring kedua sisi-baca: fakta bernuansa otorisasi (diskon/refund/transfer/
  gratis) difilter dari prompt (di-log, tak diinjeksi).
- Guard `check_storable()` diperluas selektif: `\bsandi\b`, rekening/norek/IBU,
  KTP/SIM/paspor/NPWP/NIK (kata), pola kartu `no. kartu`, digit 10+ (NIK
  berformat, HP mentah), alamat presisi → alasan baru `pii_address`,
  injeksi (+`disregard`, `override/forget … instructions`, `you must now`,
  `system:/sistem:`, `kamu adalah … mulai sekarang`, leetspeak),
  interogatif tanpa `?` (ID/EN), perintah hapus varian (sufiks `-ku`, EN).
  Nominal uang (`Rp …`) dikecualikan dari aturan digit.
- Kebijakan PII diubah: HP MENTAH kini `secret` (simpan tersamar
  `08xx-xxxx-7890`); matriks di `MEMORY_POLICY` (single source; prose prompt
  di-generate + test parity anti-drift).
- `put_fact`: tolak >500 char (bukan potong), tolak key sembarang (anti
  timpa `profile`), dedup ≥0.9 (`DuplicateFactError`), kuota 200 + eviksi
  terlama best-effort, value bawa `ts` (provenance), lock per-namespace
  untuk `put_profile` (anti lost-update 1 worker).
- Tool baru `save_profile_patch` (slot allowlist: nama/kota/preferensi/
  keluarga/catatan — nilai baru menimpa, bisa dikoreksi); `MEMORY_RULES`
  dipersempit (DO/DON'T konkret); `_REFUSALS.get` + koersi input (fail-soft).
- `DELETE threads` = hapus riwayat, BUKAN ingatan (didokumentasikan README).

**SSE/API correctness (B3/B5 + MAJOR):**
- Agent tak dikenal (404) di dalam stream → event error terminal (bukan
  stream 200 terpotong); `validate_before_stream()` dipakai route sebelum
  `StreamingResponse`; `is_disconnected()` + redam CancelledError.
- Interrupt checkpoint diteruskan ke event result (`pending_approval`);
  gagal baca final → `stream_error` (bukan result kosong); guard anti
  eksekusi-ganda di agent (insight: graph jalan + hasil hilang = error,
  bukan run ulang); `started` dijamin pertama bahkan di jalur fallback.
- `coerce_llm_text` fallback tak lagi bocorkan block reasoning ke reply.
- `_to_state` dict tahan non-serializable (`default=str`, di dalam try).
- `run()` petakan Timeout/Connection → `UpstreamAIError`; `resume()` try/except.
- Controller teruskan status/code AppError asal ke `stream_error`; shape error
  seragam (`thread_id` + `request_id` top-level, didokumentasikan di stream.py).
- Job: resolve (400/403) SEBELUM lookup agent (404); bug program → 500
  INTERNAL (bukan 502, tanpa bocor nama exception); kontrak rusak → 500.
- Health: acquire koneksi ikut timeout; `/ready` → 503 bila belum ready;
  gateway "ok" hanya 2xx; `version` dari `Settings.APP_VERSION`.
- Resume route butuh identitas (tetap 501); fork cid kosong → 400.

**Observability (core #2/#3):** `RequestIDMiddleware` (echo/generate →
state + ContextVar + header respons); semua handler pakai resolve (tak pernah
`"-"`); logging idempoten (tanpa `force=True`) + filter request_id; judul
error HTTP dipetakan (404/405/401/429/...).

**Config boundary (core #4/#5/#16):** `extra="forbid"`, validator
`DATABASE_URL`/pool/URL/temperatur/timeout/CORS, `LOG_LEVEL` Literal
(case-insensitive), `APP_VERSION` tunggal, ambang middleware via Settings,
pola `cache_clear` terdokumentasi.

**Pipeline:** Decimal dipertahankan sampai presentasi (string di state);
fallback structured-gagal → render deterministik (tanpa angka LLM);
kebijakan amount (`<=0` drop, refund = expense terpisah) didokumentasikan.

**OOP/cleanup:** `TemplateChatAgent` menolak dipakai langsung; override
identik dihapus (Example beri kustomisasi nyata: cap 2000 char);
`BaseController._ensure_agent` mati dihapus; duplikat return dihapus;
link HITL + wording diperbaiki; `AgentContext` typing diketatkan.

**Infra/higiene:** `Infra/.gitignore` (`.env`); Dockerfile pakai lock +
HEALTHCHECK + CMD `python -m app.main` (flag CLI `--loop` INVALID — hanya
programmatic yang sah); `normalize_conninfo` prefix-only; clamp pool +
warning; `close_pool` log; `setup_persistence` asumsi serial terdokumentasi;
docstring `llm.py` vs ADR-11; `.env.example` lengkap (HOST/PORT/RELOAD,
CORS_*, TOOLCALL_*, CONTEXT_*); `pytest.ini` `asyncio_mode=strict`;
`tests/conftest.py` selector-policy; HOST/PORT/RELOAD jadi field Settings;
`JobResponse.data` didokumentasikan opsional; cabang ValueError→502 dihapus.

**Gelombang 2 (final review):** profil difilter otorisasi + di-quote (C1);
NIK-format 13+ digit non-ribuan ditolak walau berkedok nominal (C2);
tombstone pesan basi (C3); lock `put_fact` (M4); kuota evidence-based +
log jujur (M5); gather independen (M6); slot profil min-2-char +
PIN-butuh-digit + FORGET presisi (M7); sanitasi delimiter/quote (M8);
`/livez` liveness murah; ctx request-id di generator SSE; judul error
asli di stream; log status terminal; init-502 spesifik; run-branch SSE
tak pernah raise; `_graph_for_fork` tangkap ValueError.

**Tests:** 343 unit hijau (dari 158) + 5 integrasi: guard adversarial ID/EN +
FP + parity, 20 payload injeksi end-to-end (+residual terdokumentasi),
dedup/kuota/konkurensi/namespace (+degradasi semantik), SSE envelope
(404-dalam-stream, interrupt, no-empty-result, no-double-run, judul asli),
request-id rantai penuh (+ctx generator, /livez), validasi config,
infra unit (kwargs pool, kontrak LLM factory, phoenix no-op), pin kontrak
beku, threads HTTP-level (403/404/state/history/fork/delete), determinisme
fallback pipeline, presisi desimal (+underscore), template-guard,
tool-binding, tombstone stale, profil-otorisasi, NIK-berkedok-nominal.

**ADENDUM v0.2 (ralat F-09):** spend tracking via `extra_body.metadata`
(`spend_logs_metadata`), BUKAN header `x-litellm-spend-logs-metadata` —
jalur header terbukti tak mendarat di proxy v1.105.1. Jangan debug via header.

## v0.3 (2026-10-17) — Persistence+ (pool, memori, thread final, API state)
- BREAKING: `DATABASE_URL` wajib (app-db Postgres); kosong/tak terjangkau = tolak
  start (fail-fast). Fallback memory + `get_checkpointer(url)`/`is_postgres_configured`
  DIHAPUS. Lihat migration note di README.
- BREAKING: `thread_id` kanonis `tenant:user:session` (regex beku); format lama
  (`thr-*/run-*`) ditolak 400; header `X-Tenant-ID`/`X-User-ID` wajib; silang
  pemilik = 403. `resolve_thread_config(identity, thread)` ganti signature.
- Pool: 1 `AsyncConnectionPool` (min=2/max=10 via env) dipakai checkpointer+store;
  kwargs koneksi meniru `from_conn_string` resmi (autocommit/dict_row); buka di
  lifespan, tutup saat shutdown. Windows: selector event-loop policy (D-10) +
  entrypoint kanonis `python -m app.main` (loop selector otomatis; uvicorn CLI
  memaksa Proactor bila tanpa flag `--loop asyncio:SelectorEventLoop`; lifespan
  gagal cepat dengan pesan aksi bila loop salah).
- ADR-11: graph TETAP dibangun per request (bukan 1/worker) — instance LLM
  mengikat header spend per thread; pool yang menyelesaikan konkurensi.
- Memori prompt-driven: `MemoryStore` (namespace `tenants/{t}/users/{u}`) —
  profil merge + fakta; injeksi profil+fakta sebagai SystemMessage (fail-soft,
  berlabel anti-injeksi); keputusan simpan = LLM via tool `save_memory`
  factory-bound (aturan di deskripsi tool + blok `MEMORY_RULES`). Heuristik
  regex DIHAPUS (salah tembak pada kalimat tanya — terbukti live).
  Guard kode `check_storable()`: tolak rahasia/finansial/NIK, injeksi prompt,
  pertanyaan, permintaan hapus; HP/DOB/kota/kesehatan BOLEH (keputusan).
  Pipeline sengaja tanpa store.
- API thread: `GET state/history` (paginasi, terbaru dulu), `POST fork`
  (`{checkpoint_id, agent}`), `DELETE` → 204 (checkpoints saja; store utuh).
  404 via cek eksplisit (spike: alist/state/delete tak raise untuk asing).
- Error baru: `STORAGE_UNAVAILABLE` 503, `INVALID_THREAD_ID` 400,
  `THREAD_NOT_FOUND` 404, `CROSS_TENANT_DENIED` 403. Health: `checks.store` +
  status pool, version `v0.3`. CORS: tambah `X-Tenant-ID/X-User-ID`.
- Infra (lokal): service `app-db` (postgres:16-alpine, :5433, volume `app-pgdata`)
  TERPISAH dari `litellm-db`; kredensial `APP_DB_*` di `Infra/.env`.
- Tanpa dep baru (`AsyncPostgresStore` sudah di `langgraph-checkpoint-postgres`
  3.2.0); lock verified identik (140 paket, tanpa bump). Kontrak beku lama TAK berubah.
- Tests: 158 unit (tanpa docker, termasuk tabel natural 28 kasus guard) +
  5 integrasi (app-db live, tanpa LLM).
- Temuan code-review (diperbaiki sebelum tag): resolve thread di route (400 JSON,
  bukan SSE 200 kosong); ID stabil pesan profil (anti-duplikat); put_profile baca
  keras; filter rahasia di tool; CORS DELETE; fork tolak cid kosong;
  health probe SELECT 1; fork tahan LITELLM kosong + deepcopy; fakta dikabel ke
  prompt; namespace lowercase; lifespan tutup pool saat setup gagal.
- Temuan live-test user (diperbaiki sebelum push): heuristik tulis salah tembak
  ("siapa nama saya...?" → nama sampah) → heuristik DIHAPUS, tulis full
  prompt-driven via `save_memory` + guard `check_storable()` + aturan tolak.

## v0.2 (2026-10-15) — LiteLLM gateway + ketahanan chat + spend tracking
- BREAKING (env): hapus `MODEL_PROVIDER`/`OPENAI_*`/`OPENCODE_*`; LLM 100% via
  gateway LiteLLM (`LITELLM_BASE_URL/API_KEY/MODEL`). Lihat migration note di README.
- `infra/llm.py`: `ChatLiteLLM` + header `x-opencode-session` (thread_id) +
  `extra_body.metadata` → `spend_logs_metadata` (project+thread) — RALAT v0.3.1:
  BUKAN via header `x-litellm-spend-logs-metadata` (terbukti tak mendarat di
  proxy v1.105.1); `max_retries=0` (retry milik proxy).
- Baru: `langchain-litellm==0.11.1` (preservasi reasoning); lock regen (140 paket).
- `health`: cek proxy `/health/liveliness` (timeout 2s) → field `checks.gateway`.
- Middleware aktif: `ToolCallLimit` (15 total/5 per tool) + `ContextEditing`
  (ClearToolUses 8k/keep-3) + `ToolError` ramah-ID. Tanpa retry/fallback agen
  (milik proxy), tanpa trim ganda, tanpa PII (v0.4), HITL tetap stub (v0.6).
- CORS env-based (`CORS_ORIGINS`, default localhost:3000).
- Pipeline: `output.data` aditif (angka Python + insight LLM tervalidasi;
  self-correction 1x; gagal-lembut `data` absen + `reply` tetap ada).
- Baru: `agents/examples/OrderExtractorDemo.py` (pola `response_format`,
  di luar registry) + `extract_reasoning_text`/`split_chunk_text` adversarial-safe.
- Infra (lokal, luar repo): proxy pin `v1.105.1` + `litellm-db` + hardening
  (restart, healthcheck, mem_limit, log rotation) + `forward_client_headers_to_llm_api`.
- Kontrak beku TAK berubah (SSE envelope, `reply`, registry, route).
- Tests: 57 passed.

## v0.1 (2026-10-15) — SSE token-level + envelope thinking (BEKU)
- `Runner.stream()`: single-chunk → token-level via `astream_events` v2
  (`on_chat_model_stream`/`on_tool_start`/`on_tool_end`); final dibaca dari
  checkpoint (`aget_state`, tanpa eksekusi ulang); graph legacy tanpa
  `astream_events` fallback single-chunk.
- Event baru (beku permanen): `token {delta}`, `thinking {delta}`, `tool_start
  {tool,input}`, `tool_end {tool}`. Terminator = stream close (tanpa `[DONE]`).
- `thinking` masih placeholder (ChatOpenAI membuang reasoning; diisi v0.2).
- `MessageUtils`: baru `extract_reasoning_text()` + `split_chunk_text()`
  (delta tak di-strip); konstanta `THINKING_BLOCK_TYPES`.
- `BaseChatAgent`: lewatkan event granular apa adanya; skip fallback `run()`
  bila stream sudah tutup dengan error (cegah eksekusi tool 2x).
- Kontrak lama kompatibel: consumer `result`-only tetap dapat tepat 1 result.
- Tests: 44 passed (`test_sse_streaming` baru: urutan, error terminal,
  backward-compat, fallback legacy, no-fallback-pasca-error).

## v0 patch 2026-10-14 — Code-review fix (DRY + correctness + kontrak)
- Baru: `agents/BaseChatAgent.py` (build/stream_agent/_safe_postprocess/_to_state) — hapus ~80% duplikasi Example vs Template chat.
- Baru: `agents/BasePipelineAgent.py` (run_agent/_to_state/_wrap_narrate) — hapus 100% duplikasi run_agent pipeline.
- `Example/TemplateChatAgent`: tipis (SYSTEM_PROMPT+TOOLS+pre/postprocess saja). `Example/TemplatePipelineAgent`: node domain saja + `session_id` disamakan.
- `MessageUtils`: baru `coerce_llm_text()` (hanya block text/output_text, strip konsisten); `extract_last_ai_text` skip AIMessage tool-only.
- `ExamplePipelineAgent`: `parse_amount()` aman (tanpa ValueError mentah) + `dropped` count + hitung Decimal round-2.
- `BaseController`: pegang checkpointer/runner + `_ensure_agent` + `resolve_thread_config` + guard thread kosong; Chat/Job tipis.
- `ChatController`: log_done + init-LLM di dalam try (stream_error, bukan 500 mentah).
- `dependencies`: hapus double-cache (`get_checkpointer_cached`), tambah `get_request_id()` satu sumber.
- `main`: handler 404/405 → RFC7807 + handler 500 fallback + version "v0" + simpan checkpointer di app.state.
- `llm`: ValueError → UpstreamAIError (502 RFC7807), strip provider.
- `routes/chat`: helper request_id + header Cache-Control/X-Accel-Buffering/X-Request-ID + guard generator.
- `routes/jobs`: helper request_id + ganti assert → UpstreamAIError eksplisit.
- `schemas/chat`: input max_length=8000 + ChatResponse tandai referensi-FE.
- `MiddlewareBuilder`: warning saat fallback [] + getattr guard.
- Tests baru (16 passed): test_message_utils, test_pipeline_calc, test_refactor_contract.

## v0 patch 2026-10-14 — Pipeline robustness (NaN/inf/type-guard)
- `parse_amount`: tolak NaN/±Infinity/>1e15 (dulu `nan` → InvalidOperation 500, `inf` → JSON invalid).
- `_node_validate`: guard raw non-dict / items non-list → dropped (bukan AttributeError/TypeError).
- `_node_calculate`: guard validated/items, skip non-dict, counter `ignored_unknown`, helper `_finite_float` (tak pernah emit NaN/inf).
- `BasePipelineAgent`: `run_agent` guard data non-dict + messages None; `_to_state` `default=str` (set/object tak crash).
- Tests: 30 passed (regresi NaN/inf/wrong-type/magnitude/unknown/run_agent/to_state).

## v0 patch 2026-10-14 — OOP: ABC + AgentContext + registry
- `BasePipelineAgent(ABC)`: `build()` jadi `@abstractmethod`; 3 node `TemplatePipelineAgent` jadi `@abstractmethod` — template kosong gagal saat instantiate (TypeError), bukan saat job jalan.
- Baru: `agents/AgentContext.py` (dataclass settings/checkpointer/runner/session_id) — 1 parameter gantikan data-clump 4 arg di semua `__init__`; `BaseChatAgent`/`BasePipelineAgent`/kedua controller pakai ctx.
- Baru: `controllers/registry.py` (`CHAT_AGENTS`/`JOB_AGENTS`) — tambah agen = 1 baris, tanpa edit controller; `_ensure_agent` cek registry (rel salah → 404, mis. chat minta pipeline).
- `BaseChatAgent` tetap konkret (punya default fungsional); relasi warisan tetap 1 level.
- Tests: 37 passed (`test_oop_contract` baru: ABC block, ctx refs, registry, rel-salah-404).

## v0 (2026-10-05) — Fondasi inti
- Pola `route -> service -> runner -> agent` (FastAPI + LangGraph).
- 1 default agent (`create_agent` + `ChatOpenAI` + 1 contoh tool `check_stock`).
- `POST /chat/invoke` sync + `POST /jobs/{agent}` one-shot.
- Checkpointer: Postgres jika `DATABASE_URL` diisi, fallback `InMemorySaver` jika kosong.
- `GET /health`, error mapping (`AppError` -> HTTP), `request_id` logging.
- Slot (stub) untuk v0.1/v1: streaming SSE, `resume` HITL, RAG, auth/tenant.
- DB: Postgres saja. Tanpa Mongo, tanpa auth.

## v0 patch 2026-10-06 — Import cleanup (production-grade)
- Semua import wajib pindah ke global atas (fail-fast saat startup).
- Baru: `app/services/base.py` (`new_thread_id` + `extract_last_ai_text` bersama).
- `AsyncPostgresSaver` + `HumanInTheLoopMiddleware` tetap lazy (opsional, benar).
- Hapus fallback palsu (`tool`/`BaseState` palsu) + import `clear_cache` nganggur.

## v0 patch 2026-10-06 — Folder-per-agent
- Baru: `agents/common.py` (load prompt + middleware bersama).
- Baru: `agents/default/` (prompt/tools/agent + hooks preprocess/postprocess).
- Baru: `agents/finance_summary/` (contoh agent ke-2 + `preprocess.clean_finance_payload`).
- `registry.py` jadi spec-based (`get_agent_spec` + `get_agent` cache). `factory.py` tinggal shim.
- `chat/job_service` panggil hooks agent (`preprocess`/`postprocess`), runner tetap 1 pintu.
- `prompts/system_default.md` lama dipertahankan (duplikat `default/prompt.md`, hapus di v1).

## v0 patch 2026-10-06 — Samakan hook + keluarkan contoh finance
- Hook disamakan: `preprocess` / `postprocess` (hapus `preprocess_text`/`postprocess_text`).
- `default/agent.preprocess` terima `str | dict` (chat strip, job dict teruskan).
- Contoh `agents/finance_summary/` dikeluarkan dari boilerplate (tetap jadi pola acuan di docs/chat).
- Registry kembali 1 agent (`default`), tambah baru = tambah folder + 1 baris.

## v0 refactor 2026-10-06 — Route -> Controller -> Agent (total)
- Hapus `services/` + `registry.py`/`factory.py` + `agents/default/` (pola lama).
- Baru: `controllers/` (`base.py` BaseController + `runner.py` + `default_controller.py`).
- Baru: `agents/DefaultAgent.py` (class: `__init__` build, `run_agent`/`stream_agent` pisah,
  `preprocess`/`postprocess`, runner di-inject dari controller).
- Controller pegang thread_id + config + error + log; agent pegang prompt/tools/build/hooks.
- Route sama persis (`/chat/invoke`, `/jobs/{agent_name}`); `stream_agent` single-chunk (token-level di v0.1).

## v0 rebuild 2026-10-06 — Pemulihan pasca-insiden hapus folder
- Seluruh `app/` + `tests/` hilang akibat perintah bersih `__pycache__` yang salah sasaran.
- Rebuild penuh ke struktur final Controller + `run_agent`/`stream_agent` pisah (tanpa perintah hapus).
- Tidak ada perubahan desain; ini restorasi + `README` disesuaikan ke pola Controller.

## v0 patch 2026-10-06 — 1 nama function seragam (`run`/`stream`)
- `run_chat`/`run_job` gabung jadi `run(req)` (+ `agent_name` untuk jobs); `stream_chat` jadi `stream(req)`.
- Route chat dan jobs panggil nama yang sama; cabang Chat vs Job di dalam controller.
- Agent tetap `run_agent`/`stream_agent` pisah.

## v0 patch 2026-10-06 — run = job only, stream = chat only
- `run(req: JobRequest)` khusus one-shot sync (`POST /jobs/{agent}` -> JobResponse).
- `stream(req: ChatRequest)` khusus percakapan SSE (`POST /chat/stream` -> event stream).
- Hapus `_run_chat` sync; chat selalu mengalir via `stream_agent`.

## v0 tambah 2026-10-06 — 2 template pedoman agent
- `agents/TemplateChatAgent.py` + `prompts/template_chat.md`: pedoman chatbot ReAct
  (copy → rename class/AGENT_NAME → isi prompt → pilih tools → sesuaikan preprocess/postprocess).
- `agents/TemplateStaticLanggraphAgent.py` + `prompts/template_static.md`: pedoman static flow
  kaku (`validate -> calculate -> narrate`, hitung di Python, LLM hanya narasikan).
- Keduanya pola `run_agent`/`stream_agent` pisah, runner di-inject; tidak terdaftar di route.

## v0 patch 2026-10-06 — TemplateChatAgent stream-only
- Hapus `run_agent` dari template chat (chat = streaming only); fallback pakai `runner.run` langsung.
- `DefaultAgent` tetap punya dua-duanya (melayani job sync + chat SSE); template static tetap lengkap.

## v0 patch 2026-10-06 — TemplateStaticLanggraphAgent run-only
- Hapus `stream_agent` dari template static (pipeline = sync only via `POST /jobs/...`).
- Chat template stream-only, static template run-only; `DefaultAgent` contoh hidup tetap dua-duanya.

## v0 patch 2026-10-06 — Template static jadi multi-subagent 1->2->3
- Node contoh validate/calculate diganti 3 subagent beneran (`sub1/sub2/sub3`, stateless).
- State estafet `raw_input -> result1 -> result2 -> final`; parent pegang checkpointer.
- Controller tetap 1x `run_agent()`; rantai hidup di node, bukan di controller.
- Revisi: `preprocess`/`postprocess` dihapus dari kedua orchestrator (milik member);
  `run_agent` teruskan dict + bungkus `{"reply": ...}` langsung.

## v0 refactor 2026-10-06 — Folder orchestrators/ + nama file = nama class
- Baru: `orchestrators/` (wiring only, tanpa prompt): `FinancePipelineOrchestrator.py`
  (contoh hidup 1->2->3), `PipelineState.py`, `TemplatePipelineOrchestrator.py` (pedoman).
- Baru: member `agents/ExtractorAgent.py`, `CalculatorAgent.py`, `NarratorAgent.py`
  (1 file = 1 class, prompt+tools+preprocess/postprocess sendiri) + promptsnya.
- Hapus `agents/TemplateStaticLanggraphAgent.py` + `prompts/template_static.md` (pindah konsep).
- Aturan: `agents/` = berpikir (prompt), `orchestrators/` = giliran (edge); hapus kata "subagent".

## v0 refactor 2026-10-06 — 2 rel Template/Example, tanpa Default/orchestrators
- Rel chat: `TemplateChatAgent.py` (pedoman, stream-only) + `ExampleChatAgent.py` (hidup).
- Rel pipeline: `TemplatePipelineAgent.py` (pedoman) + `ExamplePipelineAgent.py`
  (hidup, self-contained validate->calculate->narrate) — di `agents/`.
- Hapus: `DefaultAgent.py`, `default_controller.py`, member
  (`Extractor/Calculator/NarratorAgent.py`), folder `orchestrators/`.
- `ChatController` (stream) + `JobController` (run, `example_pipeline`); README ikut 2 rel.

## v0 patch 2026-10-06 — State jadi folder
- `agents/state.py` → `agents/state/` (`BaseState.py` + `__init__.py` re-export).
- Import tidak berubah (`from app.agents.state import BaseState`); state baru tambah file di folder itu.

## v0 refactor 2026-10-06 — Prompt inline di file Agent
- Semua prompt `.md` dipindah jadi konstanta `SYSTEM_PROMPT` di file agent masing-masing.
- Hapus folder `agents/prompts/` + helper `load_prompt_md` (copy 1 file = prompt ikut, tanpa path).
- Aturan: inline default; cabut ke `.md` hanya jika prompt >~50 baris/shared/diedit non-dev.

## v0 refactor 2026-10-06 — common.py pecah jadi utils/
- `agents/common.py` → `agents/utils/` (`MiddlewareBuilder.py` + `MessageUtils.py`).
- 7 file pengimpor diperbarui; tidak ada sisa referensi `agents.common`.

## v0 refactor 2026-10-06 — Nama file = nama ClassName di 4 folder
- `utils/MiddlewareBuilder.py` → `MiddlewareBuilderUtils.py`.
- `tools/example_search.py` → `ExampleSearchTool.py`; `tools/registry.py` → `ToolRegistry.py`.
- `middleware/hitl_policy.py` → `HumanInTheLoopMiddleware.py`.
- Import di 5 file diperbarui; `state/` + `MessageUtils.py` sudah sesuai sejak awal.

## v0 patch 2026-10-06 — Orchestrator state jadi folder
- `orchestrators/PipelineState.py` → `orchestrators/state/` (`PipelineState.py` + `__init__.py`).
- 2 pengimpor (Finance + Template orchestrator) pindah ke `from app.orchestrators.state import ...`.

## v0 tambah 2026-10-10 — Provider OpenCode Go + uji hidup pertama
- `MODEL_PROVIDER=opencode_go`: `ChatOpenAI` ke `OPENCODE_BASE_URL` + `OPENCODE_MODEL`.
- Perbaikan dari uji live: base_url TANPA `/chat/completions` (client menambah otomatis);
  header `x-opencode-session` = thread_id (wajib oleh Go) + user-agent app.
- Controller rakit agent per eksekusi agar session ikut thread (tanpa biaya tambahan).
- Terverifikasi: `POST /jobs/example_pipeline` completed (hitung benar) dan
  `/chat/stream` SSE + tool `check_stock` via `deepseek-v4.1-flash`.

## v0 tambah 2026-10-10 — Standar respons (RFC 7807 + meta)
- Error selaras RFC 7807: `{type, title, status, detail, instance, code}`,
  `status` == HTTP status, `instance` = request_id. Terverifikasi live (404).
- Respons sukses tambah `meta: {request_id, duration_ms}` (`JobResponse`,
  `ChatResponse`, event stream sertakan `request_id`).
- README: `thread_id` didokumentasikan sebagai idempotency key.
- 422 validasi Pydantic ikut bentuk RFC (`VALIDATION_ERROR` + ringkasan +
  per-field `errors`); terverifikasi live untuk kedua kasus screenshot.

## v0 tambah 2026-10-10 — Helper respons terpusat (app/helpers/response.py)
- `schemas/` steril models only (`stream.py`: StreamStarted/Result/Error).
- Builder: `success_response()` (suntik meta), `stream_started/result/error()`,
  `error_problem()` (1 sumber bentuk error sync + SSE terminal).
- `JobController`/`errors.py`/`runner`/kedua agent chat + `ChatController`
  (tutup mid-stream gagal dengan event error) pakai builders. Bentuk JSON tak berubah.

## v0 patch 2026-10-10 — Nama builder Set A (rel + hasil)
- `success_response` → `job_success`, `error_problem` → `job_error` (+ inti `_problem`),
  `stream_result` → `stream_success`. `stream_started/error` tetap.
- 6 file pemakai diperbarui (termasuk docstring); bentuk JSON tak berubah.

## v0 tambah 2026-10-10 — Observability Phoenix (v0.1 tracing, di v0)
- `infra/phoenix.py`: setup sekali di lifespan (no-op jika disabled/belum install).
- `thread_id` otomatis jadi session Phoenix via runner (1 thread = 1 percakapan di UI).
- Config `.env` + service `docker-compose` (UI :6006). Mati default; verifikasi:
  compile bersih + mode disabled no-op tanpa paket terinstall.
- Revisi pola resmi docs: `phoenix.otel.register(auto_instrument=True)` gantikan
  rakitan OTel manual; paket `arize-phoenix` → `arize-phoenix-otel` (ringan).
- Terverifikasi live (screenshot): trace `POST /jobs/...` ber-node
  validate/calculate/narrate + ChatOpenAI/ChatCompletion; tab Sessions mengelompok
  per `thread_id` (1 turn, token terhitung).

## v0 refactor 2026-10-10 — Phoenix pindah ke folder Infra/ bersama
- Service `phoenix` keluar dari `Boilerplate/v0/docker-compose.yml` → `Infra/docker-compose.yml`
  (1 stack untuk semua project; + slot `redis` dikomen + `Infra/README.md`).
- Endpoint BE ke Phoenix via `${PHOENIX_ENDPOINT}` (default host.docker.internal untuk container).

## v0 patch 2026-10-10 — Default aman untuk non-rahasia (temuan code-review)
- `PHOENIX_*` dapat default (URL loopback + nama project publik bukan rahasia);
  `.env` lama tanpanya tetap start (degraded). Terbukti in-process.
- Komentar stale base_url diperjelas. Rahasia (`*_API_KEY`) tetap kosong-wajib-dari-env.
