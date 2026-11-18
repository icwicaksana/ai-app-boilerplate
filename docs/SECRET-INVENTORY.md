# Inventaris secret v1.0

Semua secret = ENV (tak pernah di file/commit). CI secret-scan menjaga.

| Secret | Dipakai | Wajib prod? | Rotasi |
|---|---|---|---|
| `JWT_SECRET` (≥32 char) | sign/verify JWT | YA | revoke semua sesi (tak ada denylist refresh selain logout) |
| `DATABASE_URL` (password di URL) | pool app + worker | YA | update → restart |
| `CACHE_URL` (password) | Valkey client | bila cache dipakai | update → restart |
| `QDRANT_API_KEY` | Qdrant Cloud/lokal ber-key | bila Qdrant ber-key | update → restart |
| `LITELLM_API_KEY` | gateway LLM | YA (bila chat/jobs LLM) | update → restart |
| `LITELLM_MASTER_KEY` (infra) | server LiteLLM sendiri | sisi infra | sisi infra |
| `METRICS_TOKEN` (baru v1.0) | guard `/metrics` | YA (else loopback saja) | update → restart |
| `APP_DB_PASSWORD` (infra) | user DB app | sisi infra | sisi infra + `DATABASE_URL` |

Aturan:

1. Contoh nilai HANYA di `.env.example` (kosong/contoh non-rahasia).
2. Secret-scan CI gagal bila pola secret muncul di diff.
3. Log yang aman: `last_error`/`trace` ter-redact (F-I3); tiket WS
   ter-redact (F-I2); password tak pernah di-log (review V1-B).
4. Password user = Argon2id (`pwdlib`), tak reversible.
