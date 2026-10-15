# Changelog Boilerplate

Semua perubahan template dicatat di sini. Rilis = git tag (`v0`, `v0.1`, ...).

## v0.2 (2026-10-15) — LiteLLM gateway + ketahanan chat + spend tracking
- BREAKING (env): hapus `MODEL_PROVIDER`/`OPENAI_*`/`OPENCODE_*`; LLM 100% via
  gateway LiteLLM (`LITELLM_BASE_URL/API_KEY/MODEL`). Lihat migration note di README.
- `infra/llm.py`: `ChatLiteLLM` + header `x-opencode-session` (thread_id) +
  `x-litellm-spend-logs-metadata` (project+thread); `max_retries=0` (retry milik proxy).
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
