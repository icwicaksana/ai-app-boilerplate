# Runbook: cache-down / fail-open (S9)

Alarm: `cache_unavailable` di log + header `X-Degraded: cache` di respons,
atau panel Grafana "Error ratio" naik dengan sebab cache.

## 1. Kenali mode

Fail-open by design (ADR-16): cache mati = fitur degradasi, BUKAN 500.

| Fitur | Tanpa cache |
|---|---|
| Rate-limit | **mati** (semua lewat) — risiko abuse |
| Idempotency chat/jobs | **mati** (double-submit dieksekusi 2×) |
| Turn-resume SSE | fallback DB bila ada, else 503 jujur |
| Tiket WS | 503 jujur (sekali-pakai butuh simpan) |
| Board/presence WS | nonaktif |

## 2. Diagnosis (2 menit)

1. `GET /api/v1/ready` → 200 (app hidup; ready hanya probe DB).
2. `GET /api/v1/health` (JWT) → `cache: "down..."`.
3. Valkey: `redis-cli -h <host> -a <pass> PING` → harus `PONG`.
4. Cek `CACHE_URL` di env deployment (password/rotasi?).

## 3. Remediasi

- Valkey mati: restart service; app reconnect otomatis (lazy client).
- Password salah/rotasi: update secret → rolling restart.
- Jaringan: cek security-group/firewall 6379.

## 4. Selama insiden (bila lama)

- Risiko utama = rate-limit mati → nyalakan throttle di LB/WAF sementara.
- Idempotency mati → beri tahu klien agar retry dengan backoff (bukan spam).
- JANGAN restart app berulang (tak membantu; pool DB justru churn).

## 5. Verifikasi pulih

`GET /api/v1/health` → `cache: "valkey"`; header `X-Degraded` hilang;
test chaos: `pytest tests/test_chaos_cache_down.py`.
