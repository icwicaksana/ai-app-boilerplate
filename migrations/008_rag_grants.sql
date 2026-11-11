-- Migrasi 008: grant tabel untuk role fungsi privileged (temuan uji worker).
--
-- Fungsi SECURITY DEFINER (`claim_ingest_job`, `get_source_secret`) berjalan
-- sebagai owner `rag_admin` — ownership fungsi TIDAK memberi hak tabel.
-- Tanpa grant ini klaim lease gagal `permission denied` (D19-2).
-- Idempotent: GRANT boleh diulang.

GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_jobs TO rag_admin;
GRANT SELECT ON source_connections TO rag_admin;
