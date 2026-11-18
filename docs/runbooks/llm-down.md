# Runbook: LLM-down / gateway error

Alarm: `llm_errors_total` naik, atau chat SSE `stream_error` massal,
atau panel "LLM errors by agent".

## 1. Kenali dampak

- Chat (`rag_chat`/`example_chat`): turn gagal dengan error jujur
  (`UPSTREAM_ERROR`, 502) — TIDAK pernah jawaban karangan.
- Jobs `rag_answer`: status `failed` + reply kosong; `failed` terlihat di
  GET job (R: status failed + reply).
- Retrieval TANPA LLM tetap jalan (debug-search, gold).

## 2. Diagnosis (3 menit)

1. `GET /api/v1/health` (JWT) → `gateway: "down..."` atau `"ok"`.
2. Langsung ke LiteLLM: `curl $LITELLM_BASE_URL/health` (+ key bila perlu).
3. Cek kode error dominan di `llm_errors_total{code}`:
   - `auth` → API key kedaluwarsa/salah → rotasi `LITELLM_API_KEY`.
   - `timeout` → model lambat/antre → naikkan timeout? (default 60 dtk)
     atau ganti model.
   - `5xx/overloaded` → provider down → tunggu / failover model.

## 3. Remediasi

- Key: update secret → restart (env only, tanpa migrasi).
- Model: ubah `LITELLM_MODEL` → restart → verifikasi 1 chat.
- Provider regional down: ganti base URL ke region lain bila ada.

## 4. Selama insiden

- Komunikasikan: "AI lambat/gagal, dokumen & pencarian tetap jalan".
- JANGAN naikkan `max_tokens`/concurrency (memperparah antre).

## 5. Verifikasi pulih

1 chat SSE selesai (`status: completed`), `llm_errors_total` datar.
