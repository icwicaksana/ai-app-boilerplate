# AI App Boilerplate — Backend Template (FastAPI + LangChain + LangGraph)

Template backend AI Apps production-grade untuk klien UMKM/SMB/Startup.
Pola: `Route -> Controller -> Agent`. Lihat `CHANGELOG.md` untuk riwayat versi.

> Rilis ditandai **git tag** (`v0`, `v0.1`, ...). Butuh versi lama? `git checkout v0`.
> Kirim ke klien? `.\export.ps1 v0.1` → zip bersih tanpa `.git`.

## Mulai cepat (dari clone fresh)

```powershell
git clone https://github.com/icwicaksana/ai-app-boilerplate.git
cd ai-app-boilerplate
.\setup.ps1            # buat .venv + install + pytest (copy .env.example → .env)
# ISI API KEY di .env dulu, lalu:
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Cek:

```bash
curl http://localhost:8000/api/v1/health
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"agent":"example_chat","input":"stok ITEM-99 berapa?"}'
curl -X POST http://localhost:8000/api/v1/jobs/example_pipeline \
  -H "Content-Type: application/json" \
  -d '{"payload": {"items": []}}'
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
* `app/infra/` — teknis: Postgres checkpointer, `ChatOpenAI` factory, Phoenix tracing.
* `app/core/` — config, logging, error, dependencies. `app/schemas/` — kontrak Pydantic.
* `app/helpers/response.py` — satu-satunya cara merakit respons (lihat bawah).

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
| `DATABASE_URL` | kosong (memory) | Jika diisi `postgresql...`, checkpointer pakai Postgres. Jika kosong, pakai `InMemorySaver` agar bisa jalan tanpa docker. |
| `OPENAI_API_KEY` | wajib | Untuk `ChatOpenAI` (cabang `openai`). |
| `MODEL_PROVIDER` | `openai` | `openai` \| `opencode_go`. |
| `MODEL_NAME` / `OPENCODE_*` | lihat `.env.example` | Model + kredensial per provider. |
| `HITL_ENABLED` | `false` | v0 selalu `false`. Slot untuk v1 (`HumanInTheLoopMiddleware`). |
| `LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |
| `PHOENIX_ENABLED` | `false` | `true` = kirim trace ke Phoenix (butuh service phoenix jalan) |
| `PHOENIX_ENDPOINT` | `http://localhost:6006/v1/traces` | OTLP collector |
| `PHOENIX_PROJECT` | `boilerplate-v0` | Nama project di UI Phoenix |

## Endpoint

| Method & Path | Fungsi |
|---|---|
| `GET /api/v1/health` (+ `/ready`) | Liveness + cek DB/LLM (degraded, bukan 500). |
| `POST /api/v1/chat/stream` | Chat SSE token-level (v0.1). `{"agent","thread_id?","input"}` -> event `data: {...}` |

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
    main.py
    core/               # config, logging, errors, dependencies
    api/v1/routes/      # health, chat (SSE), jobs, resume (tipis)
    schemas/            # kontrak request/response (Pydantic)
    controllers/        # base, registry, runner, ChatController, JobController
    helpers/            # response.py: job_success/error + stream_* builders
    agents/             # BaseChatAgent/BasePipelineAgent + AgentContext,
                        # Example/Template chat + pipeline,
                        # utils/, state/, tools/, middleware/
    infra/              # postgres (checkpointer), llm (factory), phoenix (tracing)
  tests/                # health, kontrak, pipeline, message utils, OOP, SSE
  requirements.txt (+ .lock saat rilis)
  docker-compose.yml    # api + postgres
  Dockerfile
  .env.example
  setup.ps1 / export.ps1
  CHANGELOG.md
```

## Catatan (yang sengaja belum ada)

* Tanpa auth / multi-tenant (disiapkan di schema `metadata`).
* Tanpa MongoDB (Postgres saja).
* Tanpa vectorstore / RAG (slot di `tools/`).
* Event `thinking` belum dipancarkan (diisi reasoning di v0.2).
* HITL OFF: middleware + `resume.py` hanya stub.
* Single-gateway LiteLLM di v0.2 (cabang direct dihapus di sana).

## Observability Phoenix (opsional)

```bash
# jalankan Phoenix di mana saja (UI http://localhost:6006)
pip install -r requirements.txt   # termasuk paket Phoenix/OTel
# .env: PHOENIX_ENABLED=true
uvicorn app.main:app --reload
# Hit endpoint seperti biasa → buka UI → cari session = thread_id
```

Mati default (`PHOENIX_ENABLED=false`): app jalan normal tanpa tracing.
`thread_id` otomatis jadi session Phoenix (1 thread = 1 percakapan di UI).
