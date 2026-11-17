# Rencana v1.0 — Production-grade + evals

Status: **dieksekusi** (mulai 2026-11-17). Basis: v0.9.0 (`3a48df1`).

Dokumen ini kontrak eksekusi: setiap temuan review yang masuk fase HARUS
ditutup dengan test + bukti, atau diberi pengecualian tertulis dengan sunset.
Prinsip roadmap berlaku: freeze fitur (satu-satunya "fitur" adalah yang
wajib agar kontrak jujur — mis. hapus stub, bukan tambah endpoint).

---

## 0. Keputusan yang sudah dikunci

| # | Keputusan | Sumber |
|---|---|---|
| K1 | Route stub `POST /jobs/{thread_id}/resume` (501) DIHAPUS | user 2026-11-17 |
| K2 | Skema versi tetap `v1.0`; `/api/v1` beku kompatibel | user 2026-11-17 |
| K3 | Fail-open cache dipertahankan (ADR-16) + alarm/metrik/uji chaos wajib | user 2026-11-17 |
| K4 | Fitur tunda tetap di luar (Supervisor, DeepAgents, Text-to-SQL, router, hot-reload, eksekusi `scripts/`) | user 2026-11-17 |
| K5 | Qdrant DIDUKUNG PENUH: lexical harus di-ranking (BM25 client-side min.), divergensi diukur | user 2026-11-17 |
| K6 | `access_level` = ACL keamanan: clearance dari JWT/identitas jadi filter wajib di kedua agen + kedua cabang | user 2026-11-17 |
| K7 | LLM-as-judge TIDAK BOLEH jadi gate CI (observabilitas sampled saja) | expert Prompt, diterima |
| K8 | Load test = k6 primer; LLM stub untuk kapasitas, live kecil untuk kalibrasi | expert DevOps, diterima |
| K9 | Onboarding <15 mnt = acceptance (compose terpadu + seed + stub bila perlu) | roadmap, dipertahankan |

---

## 1. Hasil review 4 expert (ringkas + verifikasi)

| Expert | Cakupan | Temuan |
|---|---|---|
| Code Reviewer | clean code + logic app/ | 10 P0, 19 P1, 8 P2 |
| Software Architect | 6 alur end-to-end | 5 P0, 13 P1, 10 P2 |
| Penetration Tester | sudut penyerang (5 kategori) | 8 High, 15 Medium |
| RAG Pipeline Engineer | logic retrieval v0.7/v0.8 | 7 P0, 36 P1, 24 P2 |

Verifikasi spot-check penulis (6/6 TERBUKTI BENAR sebelum triage):
`DelegationTool.failures` per-build · WS tanpa pool/rag · reject tanpa resume ·
case-mismatch users-vs-threads · ingest `row_ids` tanpa GUC · `require_scope`
tak pernah dipanggil. Sisanya diterima sebagai hipotesis kerja — tiap item
wajib dibuktikan dengan test reproduksi saat eksekusi, dan temuan yang
terbukti false positive dicatat di §6 (bukan dikerjakan diam-diam).

---

## 2. Register temuan gabungan (dedup antar expert)

### P0 — blokir tag (state macet / bocor / salah perilaku)

**Alur & state (Arch + Code):**
- R1 approve→resume gagal = `approved` tapi graph pause, tak bisa retry (Arch P0-2). → klaim+resume transaksional (`resuming`, boleh retry) ATAU endpoint retry-resume.
- R2 reject tak pernah resume → thread macet di interrupt (Arch P0-3, terverifikasi). → resume dengan decisions=reject + guard 409 chat-baru-di-thread-pending.
- R3 `pending_approval` hilang bila disconnect pre-terminal (Arch P0-1). → hook pencatatan di produsen, bukan konsumen.
- R4 WS tanpa TurnLog: disconnect = kerja hilang, tak bisa resume (Arch P0-4). → WS `turns.start` + kirim `turn_id`; jangan cancel run saat disconnect.
- R5 idempotency `pending` 24 jam tanpa lease (Arch P0-5). → TTL pending pendek + heartbeat ATAU steal-by-owner + compare-and-delete abort.
- R6 TOCTOU cek-stale vs resume (Code P0-7). → cek ulang hash sesudah klaim, sebelum resume.
- R7 overwrite hash approval pending via ON CONFLICT (Arch P1-6, naik ke P0: menembus TOCTOU). → jangan overwrite; revisi baru atau tolak + audit.
- R8 resume multi-interrupt salah alamat (Arch P1-7). → map per-`interrupt_id`, atau tolak decide bila >1 interrupt.
- R9 hapus dokumen partial (Code P0-5). → backend best-effort + tetap tombstone/cancel + ringkasan.
- R10 dedup konkuren bikin job ganda (Code P0-6). → reuse-or-create atomik.
- R11 WS resume timpa run aktif (Code P0-8). → tolak 409 atau cancel-eksplisit-ditunggu.
- R12 mid-stream selalu 500 + bocor pesan (Code P0-3 + Pentest F-I1). → satu mapper: AppError teruskan status/code; tak dikenal = nama tipe saja. Berlaku Runner/Base/turns/WS.
- R13 kontrak pre-run beda cache on/off (Code P0-4 + Arch P1-3). → validasi penuh sebelum `turns.start`; replay byte-sama.
- R14 pemetaan upstream sempit → 500 bukan 502 (Code P0-10). → mapper terpusat keluarga transport/SDK/5xx + test per keluarga.
- R15 failures subagent lintas-panggilan (Code P0-1, terverifikasi, bug v0.9). → failures per-invokasi (contextvar/run-state).

**Auth & isolasi (Pentest + AppSec):**
- S1 kolisi case Budi/budi berbagi thread+memori (F-A2 High, terverifikasi polanya). → normalisasi tunggal di tulis + unique index LOWER + tolak klaim tak-kanonis.
- S2 approval lintas-user tanpa peran (F-A3 High). → ikat owner + scope `approve` + SoD (actor ≠ requester).
- S3 resume turn lintas-user (F-A4 Med→P0: baca output korban). → kunci turn ke (workspace,user) + verifikasi owner.
- S4 scopes tak ditegakkan (F-A5 Med→P0: rasa aman palsu). → pasang `require_scope` per-router ATAU hapus kolom scopes.
- S5 refresh race + reuse tak terdeteksi (F-A1 High). → klaim atomik Lua + fail-closed refresh ATAU reuse-detection revoke-keluarga.
- S6 WS: tanpa rate-limit (Code P0-9 + F-D1), ticket tanpa limit/di-URL/tak terikat (F-A8), revoke tak berlaku mid-connection (AppSec P0-6). → bucket per-pesan + ticket ketat + keputusan umur/cek-periodik.
- S7 jalur WS kehilangan binding (Code P0-2 + Arch P1-5, terverifikasi). → samakan wiring pool/rag + guard checkpointer + test matriks transport×agen.
- S8 rate-limit: tanpa trusted-proxy + endpoint auth tak ter-cover (AppSec P0-7). → XFF allowlist + matriks cakupan (refresh/ticket/me/threads/approvals).
- S9 fail-open tanpa alarm (AppSec P0-4 + F-A7/F-D6). → metrik + alert + uji chaos + runbook; bedakan endpoint sensitif.
- S10 `/health|/ready` tanpa auth = DoS downstream + rekonesaisi (F-D3 High + F-I2). → livez publik murah; health/ready auth/internal + cache + rate-limit.

**RAG/KG (RAG eng, K5+K6 berlaku):**
- G1 edge tertulis `access_level=public` (P0-1). → ACL dari chunk/dokumen (SoR), hapus/abaikan dari output ekstraktor + backfill + contract test.
- G2 provenance tanpa filter ACL (P0-2). → teruskan filter ke fetch; hop tak-lolos gugurkan path.
- G3 `row_ids` tanpa RLS/GUC (P0-3, terverifikasi). → transaksi + SET LOCAL + live test role-non-bypass + audit query mentah worker.
- G4 reindex campur-model (P0-4). → validasi `embedding_model` dominan == pin config, abort bila beda.
- G5 degraded tanpa floor disajikan normal (P0-5). → floor skala-RRF terpisah ATAU `grounded=False` + banner eksplisit.
- G6 agen tak kirim filter (P0-6 + K6). → clearance JWT → filter wajib kedua agen + kedua cabang + test isolasi.
- G7 lexical Qdrant tanpa ranking (P0-7 + K5). → BM25 client-side min. + ukur delta recall vs pgvector.
- G8 respons rerank korup → 500 (P1-5, naik ke P0: langgar janji degradasi). → perlakukan sebagai `rerank_unavailable`.

**Rilis & supply-chain (AppSec+DevOps):**
- B1 `FROM` tanpa digest (P0-1). → pin digest + bump terjadwal.
- B2 lock tercemar dev + tanpa audit (P0-2). → split lock runtime/dev + pip-audit/grype gate + SBOM.
- B3 `ARG TORCH_VERSION` bisa menyimpang + tanpa hash (P0-10). → assert == lock atau hapus ARG.
- B4 secret-scan hilang (P0-8). → pindai histori + gate CI + inventori secret + rotasi.
- B5 kebijakan semver hilang (P0-9). → 1 halaman (8 butir AppSec) + selaraskan APP_VERSION/env/docs/tag.

### P1 — wajib sebelum GA (diringkas; detail di laporan expert)

Auth: rotasi JWT tanpa prosedur · Idempotency-Key tanpa validasi + chat double-submit · redaksi tiket di log · login timing oracle (tambah hash-dummy) · livez-vs-ready + start-period.
Alur: Job lewat factory (hapus duplikasi retriever) · fork telan ValueError · fork 400-bukan-500 · status job failed + reply · hook approval non-blokir · rate-limit threads · approvals filter+total · validasi dokumen di controller · `_to_doc_response` defensif · `/ready` murah · discover-skill OSError · Brief budget mati · preview tool non-string · EDIT hapus-atau-implementasi · kode mati · blob async · orphan SSE terminal · envelope started · unifikasi result · dedup status/lang/kode · thread_id case di hash · replay meta · approval yatim · WS backpressure/presence/ticket-503 · interrupt_id kosong · fork buang interrupt · progress poll boros · finish_job vs deleted · JobController factory · X-Request-ID konsisten.
RAG: RRF hukum graph · seed threshold · perluasan rerank · cap sepakat · `graph_score_floor=0` default · kalibrasi floor Infinity (BUKTIKAN dari artefak, bukan komentar) · timeout rerank · query kosong · validasi model-identitas · angka-skala lexical+grounding (P1-11/12) · heuristik digit · dukungan digit kebetulan · near-refusal · provenance parsial · gpath_id stabil · ambang singleton · BFS deterministik (ORDER BY atau cabut klaim) · preseed batas-kata · resolve ganda · cap L3 per-batch (BENERIN ke per-doc) · trigger L3 · kuota per-proses + `GRAPH_QUOTA_EDGES` mati · L3 ditelan · cache L3 onto-version + confidence · reindex content_norm/NULL-vector/RLS-discovery · validasi triple · pin versi embedding · index Qdrant error · HNSW/kuantisasi delta · fallback content_norm · chat postprocess · tool fail-soft klasifikasi.
Prompt-guard: output-guard dump-prompt (F-P2) · perluas leksikon memori (F-P3) · perluas spoof-label + NFKC (F-P4) · quoting task subagent (F-P5) · paket adversarial multibahasa (naikkan dari 18).
Injeksi-dokumen (F-P1): hirarki instruksi + output-guard + red-team encoded — minimal hirarki + suite leak, isolasi per-doc-ACL ikut K6.

### P2 — backlog (tidak blokir tag; jadwalkan pasca-v1.0 bila waktu mepet)

Pecah build()/WS/delete/health · history keyset (catatan: F-D2 menuntut minimal paginasi/guard SEBELUM v1.0 bila fork/history dilimit — ikut P1 sebagai guard, keyset penuh boleh P2) · sort kanonis · hash-diff · depth config · log_done job · tasks-key · audit ganda · fork provenance · schema hasil · dsb. (daftar penuh di laporan).

---

## 3. Fase eksekusi

| Fase | Isi | Gate keluar |
|---|---|---|
| **V1-A fondasi rilis** | K1 hapus resume-stub · B1 digest · B3 TORCH assert · B2 split lock + audit gate + SBOM · B4 secret-scan + inventori · B5 semver 1 hlmn · ci.yml (unit+build+security) · perbaiki drift env/docs | build reproduksibel + CI hijau + kebijakan ada |
| **V1-B auth & isolasi** | S1 case · S2 approval owner/scope/SoD · S3 resume owner · S4 scopes · S5 refresh atomik · S6 WS limit/ticket/umur · S7 wiring WS · S8 proxy+matriks · S9 alarm+chaos · S10 health auth + F-I2 redact · F-I1 stream-leak + F-I3 last_error/trace + F-I4 redact | seluruh P0-S + F-I1 hijau + uji chaos cache-down |
| **V1-C koreksi alur** | R1..R15 (approval/state/turn/idempotency/WS-run/delete/dedup/error-map/subagent-failures) + P1 alur | 6 alur Arch hijau + kontrak SSE seragam + test matriks |
| **V1-D retrieval benar** | G1..G8 + K6 filter wajib + K5 ranking Qdrant + P1-RAG (kalibrasi floor BUKTI, angka-skala, determinisme, kuota L3/edge, paritas reindex, pin versi) + F-P1 hirarki/output-guard | gold-30 + gold-graph + eval RAG hijau + artefak kalibrasi |
| **V1-E evals/** | common/ (skema/runner/seed/gateway/metrics) + hitl_demo → pipeline → rag_answer → chat-tools → rag_chat; canary PR + live nightly; judge sekunder | tiap agen dataset + baseline tercatat |
| **V1-F SLO & observasi** | middleware RED + /metrics guard · SLO dua-lapis · k6 5 skenario · dashboard · 6 runbook (+ runbook fail-open/LLM-down/ingest-stuck) | angka SLO teruji + alert ber-runbook |
| **V1-G rilis** | docs final + migration v0→v1 + inventory secret · onboarding fresh-clone <15 mnt · security re-scan · tag v1.0 + push | acceptance roadmap 1–4 tercentang |

Aturan tiap fase (warisan v0.9): unit hijau + guard bersih + commit. Setiap
item P0 ditutup dengan test reproduksi dari skenario expert.

## 4. Di luar cakupan (K4, backlog v1.1+)

Supervisor · DeepAgents · Text-to-SQL · router skill · hot-reload ·
eksekusi `scripts/` (tetap DILARANG di v1.0) · spawn dinamis · upload skill
dari user · Locust · compose terpadu + LLM stub (kecuali bila menghalangi
acceptance onboarding — putuskan di V1-G).

## 5. Risiko terbesar (konsensus 3 expert)

Insiden pertama tanpa metrics, tanpa runbook, dan tanpa reproduksi lokal
<15 menit — ditutup berurutan oleh V1-A (CI/build), V1-B (alarm), V1-F,
V1-G. Risiko kedua: flakiness eval LLM — dikendalikan via K7 + canary +
ground truth tak-ambigu (pelajaran A13 v0.9).
