# AI App Boilerplate — Backend Template (FastAPI + LangChain + LangGraph)

Template backend AI Apps production-grade untuk klien UMKM/SMB/Startup.
Pola: `Route -> Controller -> Agent`. Lihat `CHANGELOG.md` untuk riwayat versi.

> Rilis ditandai **git tag** (`v0`, `v0.1`, ...). Butuh versi lama? `git checkout v0`.
> Kirim ke klien? `.\export.ps1 v0.1` → zip bersih tanpa `.git`.

## Mulai cepat (dari clone fresh)

```powershell
# 1) Nyalakan DB aplikasi (WAJIB sejak v0.3 — di repo Infra terpisah)
cd ..\Infra
docker compose up -d app-db        # localhost:5433 (isi APP_DB_* di Infra/.env dulu)

# 2) Setup BE
cd ..\ai-app-boilerplate
.\setup.ps1            # buat .venv + install + pytest unit (copy .env.example → .env)
# ISI .env dulu (DATABASE_URL + LITELLM_API_KEY + LITELLM_MODEL), lalu:
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop
```

> Flag `--loop asyncio:SelectorEventLoop` WAJIB di Windows (uvicorn default =
> Proactor yang ditolak psycopg; server gagal cepat dengan pesan aksi bila lupa).
> Di Linux/Mac flag ini no-op (sudah selector) — aman dipakai di mana saja.

Cek:

```bash
curl http://localhost:8000/api/v1/health
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: toko-a" -H "X-User-ID: budi" \
  -d '{"agent":"example_chat","input":"stok ITEM-99 berapa?"}'
curl -X POST http://localhost:8000/api/v1/jobs/example_pipeline \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: toko-a" -H "X-User-ID: budi" \
  -d '{"payload": {"items": []}}'
```

Test integrasi (butuh app-db jalan):

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://app:<pass>@localhost:5433/appdb'
.venv\Scripts\python.exe -m pytest tests/ -q -m integration
```

## Konsep (ringkas)

```text
route (HTTP tipis) -> controller (main logic AI App) -> agent (otak) -> return
```

* `app/api/` — hanya HTTP: validasi Pydantic, status code. Dilarang import LangChain.
* `app/controllers/` — `base.py` (BaseController: thread_id, config, log) + `registry.py`
  (tambah agen = 1 baris, tanpa edit controller) + `ChatController.py` (stream only)
  + `JobController.py` (run only) + `runner.py` (satu pintu ke LangGraph, di-inject ke agent).
* `app/agents/` — `BaseChatAgent`/`BasePipelineAgent` (kerangka) + `AgentContext`
  (wiring settings/checkpointer/runner/session) + 2 rel: chat (`ExampleChatAgent.py`
  hidup, `TemplateChatAgent.py` pedoman) + pipeline (`ExamplePipelineAgent.py` hidup,
  `TemplatePipelineAgent.py` pedoman, abstract), plus `utils/`, `state/`, `tools/`,
  `middleware/`. Prompt inline `SYSTEM_PROMPT`.
* `app/infra/` — teknis: Postgres checkpointer, `ChatLiteLLM` factory (via gateway), Phoenix tracing.
* `app/core/` — config, logging, error, dependencies. `app/schemas/` — kontrak Pydantic.
* `app/helpers/response.py` — satu-satunya cara merakit respons (lihat bawah).
* `app/agents/examples/` — demo pola (tidak terdaftar di registry, bukan endpoint hidup).

## Tambah AI App baru — 2 rel

* **Rel chat:** copy `app/agents/TemplateChatAgent.py` → `XxxAgent.py` (rename class +
  `AGENT_NAME` + isi prompt + pilih tools), daftarkan 1 baris di
  `app/controllers/registry.py` (`CHAT_AGENTS`). Route `/chat/stream` tidak berubah.
* **Rel pipeline:** copy `app/agents/ExamplePipelineAgent.py` → `XxxPipelineAgent.py`
  (atau `TemplatePipelineAgent.py` untuk kerangka kosong), daftarkan 1 baris di
  `registry.py` (`JOB_AGENTS`). Route `/jobs/...` tidak berubah.

## Konfigurasi penting (.env)

| Key | Default | Keterangan |
|---|---|---|
| `DATABASE_URL` | wajib | Postgres app-db, mis. `postgresql+psycopg://app:...@localhost:5433/appdb`. Kosong = app TOLAK start (fail-fast, tanpa fallback memory). |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `2` / `10` | Pool koneksi (1 pool per worker, dipakai checkpointer+store bersama). |
| `LITELLM_BASE_URL` | `http://localhost:4000` | Gateway LiteLLM (wajib jalan; tanpa fallback direct). |
| `LITELLM_API_KEY` | wajib | Virtual key per project (buat di UI proxy, BUKAN master key). |
| `LITELLM_MODEL` | wajib | Nama model di proxy (lihat UI → Models). |
| `LITELLM_PROJECT_TAG` | kosong (=PROJECT_NAME) | Atribusi spend per project. |
| `CORS_ORIGINS` | localhost:3000 | Koma-dipisah; kosong = tolak semua origin browser. |
| `HITL_ENABLED` | `false` | Selalu `false` sampai v0.6. |
| `LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |
| `PHOENIX_ENABLED` | `false` | `true` = kirim trace ke Phoenix (butuh service phoenix jalan) |
| `PHOENIX_ENDPOINT` | `http://localhost:6006/v1/traces` | OTLP collector |
| `PHOENIX_PROJECT` | `boilerplate-v0` | Nama project di UI Phoenix |

## Endpoint

| Method & Path | Fungsi |
|---|---|
| `GET /api/v1/health` (+ `/ready`) | Liveness + cek DB/LLM (degraded, bukan 500). |
| `POST /api/v1/chat/stream` | Chat SSE token-level (v0.1). `{"agent","thread_id?","input"}` -> event `data: {...}` |
| `GET /api/v1/threads/{thread_id}/state` | Snapshot terakhir thread (v0.3). `thread_id` di path wajib URL-encode (`:` → `%3A`). |
| `GET /api/v1/threads/{thread_id}/history?limit=&offset=` | Daftar checkpoint, terbaru dulu (v0.3). |
| `POST /api/v1/threads/{thread_id}/fork` | Cabang baru dari 1 checkpoint: `{"checkpoint_id","agent"}` (v0.3). |
| `DELETE /api/v1/threads/{thread_id}` | Hapus riwayat thread → `204` (profil memori TIDAK ikut terhapus) (v0.3). |

## Event SSE (v0.1, beku)

Alur: `started` -> `token`* / `thinking`* / `tool_start` / `tool_end` -> `result`
| `error` (terminal). Terminator = stream close.

| Event | Bentuk | FE |
|---|---|---|
| `started` | `{"type":"started"}` | reset bubble |
| `token` | `{"type":"token","delta":"..."}` | append ke jawaban (jangan trim!) |
| `thinking` | `{"type":"thinking","delta":"..."}` | collapsible terpisah; v0.1 belum dipancarkan (diisi v0.2) |
| `tool_start` | `{"type":"tool_start","tool":"...","input":{...}}` | indikator (mis. ⏳) |
| `tool_end` | `{"type":"tool_end","tool":"..."}` | tutup indikator |
| `result` | `{"type":"result","status","output","interrupt"}` | jawaban final (1x, kompatibel consumer v0) |
| `error` | `{"type":"error",...RFC7807}` | tampilkan + tawarkan retry thread sama |
| `POST /api/v1/jobs/{agent_name}` | Pipeline sync. `{"payload","thread_id?"}` -> `{"thread_id","status","output"}` |
| `POST /api/v1/jobs/{thread_id}/resume` | STUB. Selalu `501`. Slot untuk HITL (`Command(resume=...)`). |

## Kontrak respons (standar)

**Sukses** = bare object (tanpa envelope): `{"thread_id", "status", "output", "meta"}`.
`meta = {"request_id", "duration_ms"}` untuk tracing. `status` di sini = nasib DOMAIN
(`completed`/`pending_approval`), BUKAN HTTP status — jangan dicampur.

**Error** selaras RFC 7807: `{type, title, status, detail, instance, code}` dengan
`status` == HTTP status asli dan `instance` = request_id. `code` untuk switch di FE.

**Idempotency: `thread_id` adalah idempotency key.** Simpan di FE; saat retry/timeout
kirim ulang `thread_id` yang sama → thread dilanjutkan, bukan eksekusi ganda.
Hanya hasil sukses yang tercatat (error transient tidak di-cache).

**Merakit respons: selalu via `app/helpers/response.py`.** `job_success()` untuk
sukses sync, `stream_started/success/error()` untuk SSE, `job_error()` untuk error.
DILARANG membungkus envelope baru atau success-berisi-error (lihat docstring file itu).

## Struktur folder

```text
ai-app-boilerplate/
  app/
    main.py               # lifespan: buka pool + setup persistence (fail-fast)
    core/                 # config, logging, errors, dependencies, threads (ID kanonis)
    api/v1/routes/        # health, chat (SSE), jobs, resume (stub), threads (state)
    schemas/              # kontrak request/response (Pydantic, termasuk threads.py)
    controllers/          # base, registry, runner, Chat/Job/ThreadController
    helpers/              # response.py: job_success/error + stream_* builders
    agents/               # BaseChatAgent (+memori)/BasePipelineAgent + AgentContext,
                        # Example/Template chat + pipeline,
                        # utils/, state/, tools/, middleware/
    infra/                # postgres (pool), memory (MemoryStore), llm (factory), phoenix
  tests/                  # unit (+ integration/ yg butuh app-db live)
  requirements.txt (+ .lock saat rilis)
  Dockerfile
  .env.example
  setup.ps1 / export.ps1
  CHANGELOG.md
```

## Thread-ID + identitas (v0.3, beku)

Format kanonis: **`tenant:user:session`** (huruf kecil, contoh `toko-a:budi:sess-7f3a2c1e`).
`thread_id` kosong → server susun dari header. Format lama (`thr-xxx`) ditolak `400`.

| Header | Wajib | Keterangan |
|---|---|---|
| `X-Tenant-ID` | ya | mis. `toko-a` (3–32 char). v0.3: tepercaya; v0.4: dari JWT. |
| `X-User-ID` | ya | mis. `budi` (1–64 char). |
| `X-Request-ID` | tidak | Idempotency/tracing key (TERPISAH dari thread_id). |

Akses silang tenant/user → `403`. Thread asing → `404`.

## Memori user (v0.3)

Chat otomatis membaca profil user (`MemoryStore`, namespace `tenants/{t}/users/{u}`)
dan menyisipkannya ke prompt. Tulis terjadi dua jalur: heuristik
(`"ingat ya: ..."`, `"nama saya ..."`) + tool `save_memory` saat user meminta
eksplisit. Rahasia (password/OTP/kartu) ditolak disimpan. Store mati → chat
tetap jalan tanpa profil (fail-soft); Postgres mati → app tolak start (fail-fast).

## Catatan (yang sengaja belum ada)

* Tanpa auth penuh — identitas dari header tepercaya (JWT di v0.4).
* Tanpa Redis/cache/rate-limit (v0.4), WebSocket (v0.5), HITL aktif (v0.6).
* Tanpa vectorstore / RAG (v0.7) — `MemoryStore` hanya fakta kecil, bukan dokumen.
* Ekstraksi memori masih heuristik (LLM-based ditunda).
* HITL OFF: middleware + `resume.py` hanya stub.
* Single-gateway LiteLLM (cabang direct dihapus sejak v0.2).

## Observability Phoenix (opsional)

```bash
# jalankan Phoenix di mana saja (UI http://localhost:6006)
pip install -r requirements.txt   # termasuk paket Phoenix/OTel
# .env: PHOENIX_ENABLED=true
uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop
# Hit endpoint seperti biasa → buka UI → cari session = thread_id
```

Mati default (`PHOENIX_ENABLED=false`): app jalan normal tanpa tracing.
`thread_id` otomatis jadi session Phoenix (1 thread = 1 percakapan di UI).

## Migrasi v0.2 → v0.3 (wajib baca)

**Breaking:**

1. `DATABASE_URL` WAJIB (Postgres `app-db`). Kosong/tak terjangkau → app tolak
   start. Aksi: `cd ..\Infra; docker compose up -d app-db` (isi `APP_DB_*` di
   `Infra/.env` dulu), lalu isi `DATABASE_URL` di `.env` BE.
2. `thread_id` lama (`thr-xxx`/`run-xxx`) DITOLAK `400`. Aksi: kosongkan (server
   susun `tenant:user:sess-*`) atau kirim format kanonis; sertakan header
   `X-Tenant-ID` + `X-User-ID` di semua request chat/jobs/threads.
3. Internal (bila memanggil langsung): `resolve_thread_config(identity, thread)`,
   controller dari `request.app.state` (bukan dibangun manual),
   `get_checkpointer(url)`/helper cache DIHAPUS.

**Tidak berubah:** envelope SSE, `JobResponse`, RFC7807, registry, middleware onion,
atribusi spend proxy. Aditif: 4 endpoint `/threads/*`, tool `save_memory`
(otomatis, bukan di registry), kolom `store` di health.

## Migrasi v0.1 → v0.2 (arsip)

**Breaking (env):** `MODEL_PROVIDER`/`OPENAI_*`/`OPENCODE_*` DIHAPUS. Semua akses LLM
wajib lewat gateway LiteLLM (`LITELLM_BASE_URL/API_KEY/MODEL`). Tulis ulang `.env`
dari `.env.example` baru. Tanpa proxy jalan → chat/jobs 502 eksplisit (sengaja,
tanpa fallback — ADR-03).

**Tidak berubah:** kontrak HTTP/SSE (envelope beku v0.1 utuh), `output.reply`
pipeline, registry, route. Aditif saja: `output.data` di pipeline
(abaikan bila tak perlu), event `thinking` kini terisi untuk model reasoning.

**Middleware aktif otomatis:** limit tool (15 total/5 per tool), context-edit
(8k/keep-3), tool-error ramah. Tidak perlu config tambahan.
Spend tracking: buat virtual key per project di UI proxy, isi sebagai
`LITELLM_API_KEY`; lihat usage di UI → Usage atau `GET /spend/logs`.
