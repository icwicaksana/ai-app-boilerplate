# SLO v1.0 — dua lapis (platform vs end-to-end)

Alasan dua lapis: platform (milik kita: API, DB, cache) harus ketat dan
selalu terukur; end-to-end (termasuk LLM/embed pihak ketiga) longgar dan
diukur terpisah — insiden LLM-down tak boleh "membakar" budget platform.

## 1. Lapis platform (diukur Prometheus RED, window 30 hari)

| SLI | Target | Budget error |
|---|---|---|
| Availability `GET /ready` + rute non-LLM (2xx+4xx / total, kecuali 429) | 99.9% | 43 mnt/bln |
| Latency p95 rute sinkron non-stream (`/tokens`, `/threads*`, `/jobs` POST, `/documents` GET) | ≤ 400 ms | — |
| Latency p95 SSE first-byte (`/chat/stream`, `/ws/chat` ack) | ≤ 800 ms | — |
| Turn-resume success (`/chat/stream/{id}` 2xx / total) | 99.5% | — |

Alert: burn-rate 14.4x (1 jam) = page; 6x (6 jam) = ticket.
Runbook: `docs/runbooks/deploy-rollback.md`, `*-db-rls.md`.

## 2. Lapis end-to-end (diukur eval nightly + probe, window 7 hari)

| SLI | Target | Catatan |
|---|---|---|
| Chat jawab ≤ 30 dtk (p95, live) | 95% | termasuk LLM; LLM-down = degraded tercatat, bukan error platform |
| Retrieval hit@3 gold-30 (canary faktoid 5/5) | 100% canary | gate PR (tier canary) |
| Rag_answer grounded-rate gold (faithfulness judge ≥4) | ≥ 90% | sampled 20%, observasi (K7) |
| Ingest md→ready p95 (dok ≤1 MB) | ≤ 90 dtk | worker + embed lokal |

## 3. Angka terukur (diisi dari run staging)

| Tanggal | Skenario | p50 | p95 | err% | Catatan |
|---|---|---|---|---|---|
| 2026-11-18 | probe livez (200×, conc 10, lokal) | 15 ms | 48 ms | 0 | target ≤400ms ✓ |
| 2026-11-18 | probe ready+DB (200×) | 18 ms | 26 ms | 0 | ✓ |
| 2026-11-18 | probe auth/me (200×) | 28 ms | 62 ms | 0 | JWT+DB ✓ |
| 2026-11-18 | probe chat SSE first-byte (10×) | 117 ms | 356 ms | 0 | target ≤800ms ✓ (LLM live) |
| (staging) | k6/health | — | — | — | — |
| (staging) | k6/auth-chat-sse | — | — | — | — |
| (staging) | k6/jobs | — | — | — | — |
| (staging) | k6/documents | — | — | — | — |
| (staging) | k6/ws-chat | — | — | — | — |

Angka lokal = smoke kelayakan, BUKAN pengganti staging (hardware beda).
Cara ukur: `k6 run load/k6/<skenario>.js -e BASE_URL=... -e TOKEN=...`.
Threshold k6 = kolom Target di atas (gagal bila jebol).
