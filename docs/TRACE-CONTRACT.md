# TRACE-CONTRACT (WS3) — BEKU

Status: **BEKU** (v1). Perubahan skema = bump major + migration note.
Cakupan: chat streaming SSE/WS (`StreamResult.trace`). Jobs sync TIDAK ikut.

## 1. Skema (BEKU)

```text
trace = {
  trace_version : 1,            # int, selalu 1 di WS3
  complete      : bool,         # True = run selesai; False = jeda menunggu approval
  spans         : [span],       # urutan catat (completion order), maks 20
  truncated     : bool,         # True bila spans dipotong ke 20
  pending_approvals?: int,      # HANYA bila complete=False (n interrupt)
}

span = {
  tool          : str,          # nama tool persis (mis. "check_stock",
                                # "subagent__cek", "load_skill")
  kind          : "tool" | "subagent" | "skill",
  status        : "ok" | "partial" | "failed" | "timeout" | "skipped" | "pending",
  ms            : int,          # >= 0
  attempts      : int,          # >= 1 (delegasi/skill/tool = 1 di WS3)
  input_summary : str,          # <= 500 char, 1 baris, sudah disanitasi
  detail        : str,          # <= 500 char, sudah disanitasi ("" bila tak ada)
}
```

Batas beku: `trace_version=1`, `MAX_SPANS=20`, `input_summary/detail <= 500 char`.
Sanitasi = helper existing `neutralize_delimiters` (1 baris + cap, tanpa regex
baru). Redaksi secret TERPUSAT di `summarize_input` (helper existing
`looks_like_secret` + denylist key `password`/`sandi`/`otp`/`pin`/`nik`/
`ktp`/`api_key`/`token`, tanpa regex baru): nilai ber-key denylist →
`[redacted]`; teks bebas yang tampak secret → seluruh ringkasan jadi
`[redacted]`. Detail error tool = KELAS error saja (`ToolError` generik
untuk pesan mentah) — pesan mentah tak pernah masuk trace.

Arti status:

| status    | arti |
|-----------|------|
| `ok`      | selesai, hasil dipakai |
| `partial` | selesai sebagian (delegasi: jawaban ada + ada failure) |
| `failed`  | gagal (tool error / delegasi gagal / baca skill gagal) |
| `timeout` | delegasi melebihi budget waktu |
| `skipped` | tak dijalankan (skill unknown/traversal/file-hilang; tool start tanpa end di run completed) |
| `pending` | menunggu approval (tool start tanpa end di run `pending_approval`) |

Span ganda by-design: 1 panggilan delegasi/skill menghasilkan 2 span —
`kind=tool` (timing transport dari runner) + `kind=subagent`/`skill` (hasil
dari tool itu sendiri, nama `tool` SAMA agar bisa dikorelasikan). Baca span
kind spesifik sebagai hasil; span `tool` sebagai timing.

Realita orphan-timeout: `timeout` HANYA datang dari delegasi yang melebihi
budget waktu. Tool `start` tanpa `end` saat drain TIDAK PERNAH jadi
`timeout` — melainkan `pending` (run `pending_approval`) atau `skipped`
(run `completed`). Klien JANGAN mengartikan orphan sebagai timeout.

Larangan jumlah-ms-ganda: DILARANG menjumlah `ms` lintas span untuk total
("total waktu eksekusi"). Span bisa paralel/tumpang-tindih, dan 1 panggilan
delegasi/skill tercatat GANDA (`kind=tool` + `kind=subagent`/`skill`, tiap
span bawa `ms` sendiri) — penjumlahan menghitung ganda. `ms` hanya sah
dibaca per span.

## 2. Produsen span (BEKU)

| produsen | kind | kapan dicatat |
|----------|------|---------------|
| `runner.stream` timing FIFO per nama tool | `tool` | `on_tool_end` (ok) / `on_tool_error` (failed, trace-only, tanpa SSE, detail = kelas error saja) / orphan saat drain |
| `DelegationTool._delegate_run` | `subagent` | SEMUA jalur keluar (ok/partial/failed/timeout); brief tak-valid = `skipped` |
| `SkillTools` load/read | `skill` | SEMUA jalur keluar incl unknown/traversal (ok/skipped/failed); input unknown/traversal di-cap 64 char |

Disiplin contextvar (tiru `_current_failures`): kolektor `run_trace`
`set/append/baca/reset` HANYA di task produsen (`runner.stream` start/drain;
tool hanya `record` best-effort yang tak pernah raise). Di luar trace aktif
(jobs sync, `runner.run` langsung) `record` = no-op.

Aturan FIFO: `on_tool_start` antre per nama; `on_tool_end`/`on_tool_error`
memakai antrean TERDEPAN (cocok untuk tool paralel bernama sama).
`on_tool_end` tanpa start = span `ok` `ms=0` + detail `orphan_end`.
`on_tool_error` tanpa start = span `failed` `ms=0` + detail kelas error
(atau `orphan_error` bila info error kosong).
Event bertag `subagent`/`internal`/`no-stream` JANGAN masuk spans `tool`
(pertahanan ganda di atas `StreamPolicy`).

## 3. Otoritas: trace > kartu > reply (BEKU)

Bila ketiganya konflik, urutan kebenaran:

1. **`trace`** — fakta eksekusi (apa yang jalan, apa yang gagal, berapa lama).
2. **kartu** — kartu interrupt/approval (`interrupts[]`: aksi yang menunggu
   keputusan manusia).
3. **`reply`** — narasi LLM (`output.reply`: boleh salah/karangan).

Contoh: reply bilang "refund berhasil" tapi trace `process_refund=pending` +
kartu approval ada = aksi BELUM jalan; FE wajib tampilkan kartu, bukan reply.
`complete=False` + `pending_approvals=n` = sinyal jeda yang sah SELAMA trace
hadir (jangan parse reply untuk tahu ada approval). KOREKSI saat
`trace: null` (jalur fallback eksotik §7): trace tak ada sehingga urutan
otoritas trace > kartu > reply tak berlaku — sinyal jeda jatuh ke
`status="pending_approval"` + `interrupts[]` (kartu). `trace: null` BUKAN
bukti "tak ada approval"; klien wajib cek kartu.

## 4. Aturan abort (BEKU)

- Gagal mid-stream → event terminal `error` TANPA trace (`StreamError`
  TIDAK punya field trace; kontrak error tak berubah).
- Klien DILARANG memfabrikasi trace dari event `tool_start`/`tool_end`:
  kartu/timing SSE bukan trace (tanpa status hasil, tanpa ms akurat).
- Gagal baca checkpoint final → `error` (bukan result kosong bertrace).
- Orphan producer (restart server, turn tanpa terminal) → resume lapor
  `error`, bukan trace karangan.

## 5. Aturan resume (BEKU)

- Resume turn (`GET /chat/stream/{turn_id}`, WS `resume`) = REPLAY byte-identik
  dari turn log, termasuk `result.trace` apa adanya. Klien JANGAN merge/ubah.
- 1 turn = MAKSIMAL 1 trace (di terminal `result`). Turn `error` = tanpa trace.
- Resume-approval (`POST /approvals/{id}/decide` → `runner.resume`) adalah
  eksekusi SYNC baru TANPA trace SSE (jobs sync TIDAK ikut WS3).
  Klien JANGAN menggabung trace turn jeda (`complete=False`) dengan hasil
  resume; keduanya payload terpisah.
- `pending_approvals` = `len(interrupts)` turn itu saja (bukan akumulasi).
- Replay pra-WS3: turn yang dicatat SEBELUM WS3 deploy me-replay `result`
  TANPA field `trace` (payload lama tak punya). Klien wajib toleran:
  `trace` hilang = "tak ada data eksekusi" (sama seperti `trace: null`),
  BUKAN error parse. Jendela ini ephemeral (TTL turn per jam).

## 6. Matriks SSE/WS × cache × HITL (BEKU)

`TurnManager`/`ws.py` TIDAK diubah: trace mengalir sebagai bagian payload
event `result` sehingga otomatis ikut ke turn log + replay + WS pump.

| transport / cache | HITL `completed` | HITL `pending_approval` |
|---|---|---|
| SSE managed (cache ON) | `result.trace` (`complete=True`); replay identik | `result.trace` (`complete=False`, `pending_approvals=n`); replay identik; approval tercatat via hook produsen |
| SSE direct (cache OFF / fail-open) | `result.trace` sama; tanpa replay | `result.trace` sama; tanpa replay; hook approval tetap jalan di koneksi langsung |
| WS managed (cache ON) | `result.trace` via pump turn log; `resume` replay identik | sama + kartu approval dari `interrupts` |
| WS direct (cache OFF) | `result.trace` langsung; tanpa resume | sama; tanpa resume |

Catatan: `text`/`thinking` boleh DI-DROP di WS (backpressure) — `result`
(+trace) TIDAK PERNAH di-drop; klien yang ketinggalan wajib `resume`.

## 7. Batasan eksplisit (BEKU)

- `StreamError` / `JobResponse` / `TurnManager` / `ws.py` / system-prompt:
  TIDAK berubah di WS3.
- Jobs sync (`JobController`, `runner.run` langsung): TANPA trace.
- `trace: null` hanya sah di jalur fallback eksotik (graph tanpa event sama
  sekali → `runner.run` darurat di `BaseChatAgent`); klien perlakukan null =
  "tak ada data eksekusi", BUKAN "nol span".
- Contoh `result` minimal: `{"type":"result","status":"completed",
  "output":{"reply":"..."},"trace":{"trace_version":1,"complete":true,
  "spans":[],"truncated":false}}`.

Disambiguasi `meta` vs `trace` vs `retrieval_trace` (tiga namespace terpisah,
JANGAN dicampur):

- `trace` (dokumen ini) = fakta eksekusi chat streaming di `StreamResult`.
- `meta` (`ResponseMeta`: `request_id`, `duration_ms`) = info transport/
  request sync — TIDAK punya field `trace`, TIDAK membawa span.
- `retrieval_trace` (output `RagAnswerAgent` di `output.data`) = jejak
  observability retrieval RAG — BUKAN trace eksekusi WS3.

## 8. Kebijakan versi & kompatibilitas (BEKU)

- `trace_version` = integer skema trace. WS3 hanya menerbitkan `1`.
- WAJIB tolak-eksplisit: konsumen (`trace` di FE/BE) yang membaca `trace`
  WAJIB menolak eksplisit `trace_version` tak dikenal — perlakukan sebagai
  "tak ada data eksekusi" + catat log/telemetri; JANGAN render span dengan
  asumsi skema v1. Fail-closed: versi baru = revisi dokumen ini + bump,
  bukan parse diam-diam yang salah.
- MUST-IGNORE: field tak dikenal di `trace`/span WAJIB diabaikan (bukan
  error) — penerbit boleh menambah field aditif tanpa bump major.
- Wire key-selalu-hadir: `trace_version`, `complete`, `spans`, `truncated`
  SELALU hadir di setiap `trace` non-null. `pending_approvals` HANYA hadir
  bila `complete=False` (dan wajib ada di sana). `spans` boleh kosong `[]`
  (artinya "nol span terekam" — bedakan dari `trace: null` = "tak ada data").
