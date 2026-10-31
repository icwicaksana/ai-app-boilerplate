# AI App Boilerplate — Backend Template (FastAPI + LangChain + LangGraph)

Template backend AI Apps production-grade untuk klien UMKM/SMB/Startup.
Pola: `Route -> Controller -> Agent`. Lihat `CHANGELOG.md` untuk riwayat versi.

> Rilis ditandai **git tag** (`v0`, `v0.1`, ...). Butuh versi lama? `git checkout v0`.
> Kirim ke klien? `.\export.ps1 v0.1` → zip bersih tanpa `.git`.

## Mulai cepat (dari clone fresh)

```powershell
# 1) Nyalakan dependensi (WAJIB sejak v0.3/v0.4 — di repo Infra terpisah)
cd ..\Infra
docker compose up -d app-db cache   # app-db :5433, cache(Valkey) :6380
#    isi APP_DB_* + CACHE_PASSWORD di Infra/.env dulu (copy .env.example)

# 2) Setup BE
cd ..\ai-app-boilerplate
.\setup.ps1            # .venv + install + pytest unit + .env (JWT_SECRET digenerate)
# ISI .env: DATABASE_URL, CACHE_URL, LITELLM_API_KEY, LITELLM_MODEL

# 3) Buat akun pertama (admin/CLI — tidak ada endpoint publik)
.\.venv\Scripts\python.exe -m app.scripts.create_user `
    --tenant toko-a --username budi --password 'rahasia-kuat'

# 4) Jalan
$env:RELOAD='true'; .\.venv\Scripts\python.exe -m app.main   # dev (reload ON)
.\.venv\Scripts\python.exe -m app.main                       # prod (reload OFF)
# Host/port: $env:HOST (default 127.0.0.1; 0.0.0.0 untuk container/LAN),
# $env:PORT (default 8000).
```

> `python -m app.main` adalah cara jalan kanonis: loop selector dipilih otomatis
> di Windows (wajib psycopg; uvicorn default = Proactor yang ditolak).
> Alternatif mentah (Linux/Mac saja): `uvicorn app.main:app --reload`.
> JANGAN pakai flag CLI `--loop asyncio:SelectorEventLoop` — choices CLI
> terbatas (`auto|asyncio|uvloop|zuvloop`) dan menolak nilai itu.
> Server gagal cepat dengan pesan aksi bila loop salah (tanpa tebak-tebakan PoolTimeout).

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
  requirements.txt        # dev/longgar; image produksi pakai requirements.lock
  Dockerfile
  .env.example
  setup.ps1 / export.ps1
  CHANGELOG.md
```

## Thread-ID + identitas (v0.3, beku)

Format kanonis: **`tenant:user:session`** (huruf kecil, contoh `toko-a:budi:sess-7f3a2c1e`).
`thread_id` kosong → server susun dari identitas pemanggil. Format lama
(`thr-xxx`) ditolak `400`.

| Sumber identitas | Wajib | Keterangan |
|---|---|---|
| `Authorization: Bearer <token>` | ya (v0.4) | JWT dari `/auth/login`. Klaim `tenant` + `sub`(user) jadi identitas. |
| `X-Tenant-ID` / `X-User-ID` | ❌ tidak dipakai | Hanya untuk `AUTH_MODE=trusted_headers` (dev, butuh flag eksplisit). |
| `X-Request-ID` | tidak | Tracing key (TERPISAH dari thread_id). |

Akses silang tenant/user → `403`. Thread asing → `404`.

## Auth + proteksi API (v0.4)

```bash
# 1) buat user (admin/CLI — tidak ada endpoint publik pembuatan akun)
python -m app.scripts.create_user --tenant toko-a --username budi \
    --password 'rahasia-kuat' --scopes "chat jobs threads"

# 2) login → token pair
curl -X POST localhost:8000/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"tenant":"toko-a","username":"budi","password":"rahasia-kuat"}'

# 3) pakai token
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" \
  -d '{"agent":"example_chat","input":"stok ITEM-99 berapa?"}'

# 4) refresh (rotasi: token lama langsung mati) / logout (revoke)
curl -X POST localhost:8000/api/v1/auth/refresh -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'
```

| Proteksi | Perilaku |
|---|---|
| Rate limit | `/chat/stream` 20/menit, `/jobs/*` 10/menit, `/auth/login` 5/menit per (IP+username). Lewat batas → `429` + `Retry-After`. |
| Idempotency | `POST /jobs/*` dengan header `Idempotency-Key`: request ulang = replay (`Idempotent-Replayed: true`), payload beda = `409`, paralel = `409`. |
| Payload cap | `MAX_JOB_PAYLOAD_BYTES` (default 256 KB) → `413`. |
| Docs | `/docs` + `/openapi.json` mati default; `ENABLE_DOCS=true` untuk dev. |
| Security headers | nosniff, `X-Frame-Options: DENY`, Referrer-Policy, Permissions-Policy, COOP/CORP; HSTS hanya `ENVIRONMENT=prod`. |

Token: access 15 menit, refresh 7 hari, HS256 (alg dipin), tiap token punya
`jti` → logout/rotasi masuk denylist di Valkey sampai kedaluwarsa.

## Memori user (v0.3, prompt-driven)

Chat otomatis membaca profil + fakta user (`MemoryStore`, namespace
`tenants/{t}/users/{u}`) dan menyisipkannya ke prompt. Keputusan menyimpan =
milik LLM: bila user menyampaikan info personal baru yang tahan lama, agen
memanggil tool `save_memory` (1 fakta atomik). Tanpa regex/heuristik kaku —
pola `nama saya …` terbukti salah tembak pada kalimat tanya, sehingga dihapus.

Aturan tolak: rahasia (password/OTP/PIN/kartu/NIK/rekening), ID pemerintah,
alamat presisi, pertanyaan, permintaan hapus, dan instruksi untuk sistem.
Ditegakkan 2 lapis (aturan prompt + guard kode `check_storable`); penolakan
di-log. Fakta disuntik sebagai HumanMessage terpisah berlabel "data user,
BUKAN instruksi" (anti memory-injection; fakta bernuansa otorisasi seperti
diskon/refund difilter dari prompt). HP MENTAH dan alamat presisi (jalan +
nomor rumah / RT-RW) DITOLAK — HP tulis tersamar (`08xx-xxxx-7890`).
Store mati → chat tetap jalan tanpa profil (fail-soft); Postgres mati → app
tolak start (fail-fast).

> LLM lazy-by-design: server tetap start walau `LITELLM_*` kosong (warning di
> log) — chat pertama akan 502 sampai proxy siap. Ini disengaja agar urutan
> start docker compose bebas.
>
> Trust-header sementara: `X-Tenant-ID/X-User-ID` TANPA verifikasi sampai
> v0.4 (JWT). JANGAN expose server langsung ke publik; taruh di balik
> reverse-proxy / firewall. Server menulis warning ini tiap startup.
>
> `DELETE /threads/*` menghapus RIWAYAT, bukan ingatan (fakta/profil tetap).

## Catatan (yang sengaja belum ada)

* Tanpa auth penuh — identitas dari header tepercaya (JWT di v0.4).
* Tanpa Redis/cache/rate-limit (v0.4), WebSocket (v0.5), HITL aktif (v0.6).
* Tanpa vectorstore / RAG (v0.7) — `MemoryStore` hanya fakta kecil, bukan dokumen.
* Memori tanpa tool cek-dulu (dedup via injeksi + instruksi; revisit bila duplikat nyata).
* HITL OFF: middleware + `resume.py` hanya stub.
* Single-gateway LiteLLM (cabang direct dihapus sejak v0.2).

## Observability Phoenix (opsional)

```bash
# jalankan Phoenix di mana saja (UI http://localhost:6006)
pip install -r requirements.txt   # termasuk paket Phoenix/OTel
# .env: PHOENIX_ENABLED=true
.venv\Scripts\python.exe -m app.main
# Hit endpoint seperti biasa → buka UI → cari session = thread_id
```

Mati default (`PHOENIX_ENABLED=false`): app jalan normal tanpa tracing.
`thread_id` otomatis jadi session Phoenix (1 thread = 1 percakapan di UI).

## Migrasi v0.3.1 → v0.4 (WAJIB baca — auth breaking)

1. **Identitas pindah ke JWT.** Header `X-Tenant-ID`/`X-User-ID` TIDAK lagi
   dipercaya (default `AUTH_MODE=jwt`). Aksi:
   - nyalakan cache: `cd ..\Infra; docker compose up -d cache`
   - isi `.env`: `CACHE_URL=redis://:<CACHE_PASSWORD>@localhost:6380/0` dan
     `JWT_SECRET` (min 32 char; generate acak)
   - buat akun: `python -m app.scripts.create_user --tenant ... --username ...`
   - klien: `POST /auth/login` → simpan access+refresh → kirim
     `Authorization: Bearer <access_token>`.
   - transisi sementara (dev saja): `AUTH_MODE=trusted_headers` +
     `AUTH_ALLOW_TRUSTED_HEADERS=true` (DITOLAK bila `ENVIRONMENT=prod`).
2. **`ENVIRONMENT`** baru (`dev|staging|prod`). `prod` mewajibkan: JWT_SECRET,
   CACHE_URL, ENABLE_DOCS=false, AUTH_MODE=jwt, trusted-headers false.
3. **`ENABLE_DOCS`** default `false` → `/docs` hilang kecuali diaktifkan.
4. **`.env.example`** ditambah key v0.4 (cache/auth/rate-limit). `extra="forbid"`
   → key asing/typo = start gagal (baca pesannya).
5. **Dependency baru** (sudah di `requirements.lock`): `redis[hiredis]`,
   `pwdlib[argon2]`, `PyJWT`, `fakeredis` (test).
6. **Tidak berubah:** envelope SSE, RFC 7807, thread-ID, API threads, registry,
   middleware onion, atribusi spend. 401/409/413/429 adalah kode BARU (aditif).

## Migrasi v0.3 → v0.3.1 (patch keamanan + correctness)

**Wajib baca bila punya data memori v0.3:**

1. Format render memori berubah (fakta kini HumanMessage ter-quoting, bukan
   gabungan SystemMessage) — riwayat checkpoint lama tetap terbaca (add_messages
   replace via id stabil yang sama), profil/fakta lama tetap dipakai.
2. HP mentah kini DITOLAK (`secret`); tulis tersamar `08xx-xxxx-7890`.
   Fakta >500 char kini DITOLAK (bukan dipotong) — ringkas di sisi pemanggil.
3. `RELOAD` default kini `false` (opt-in `RELOAD=true` untuk dev);
   `HOST`/`PORT` via env; versi tunggal di `Settings.APP_VERSION`.
4. Env tak dikenal kini GAGAL start (`extra="forbid"`) — periksa typo bila
   start gagal setelah update; `DB_POOL_MAX < MIN` dan `LOG_LEVEL` salah
   juga ditolak dengan pesan jelas.

**Tidak berubah:** semua kontrak beku (SSE, RFC7807, thread-ID, threads API,
registry, onion middleware, atribusi spend).

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

