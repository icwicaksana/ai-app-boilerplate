-- 010_rag_norm_lexical.sql (v0.7.1 F3): sisi lexical ternormalisasi.
--
-- Masalah: `plainto_tsquery` me-AND-kan semua term — kata tanya ("berapa")
-- + imbuhan ("diretur" vs "retur") + angka-kata ("35 ribu" vs "35000")
-- membuat lexical miss. Worker kini menulis `content_norm` (normalize.py);
-- query lexical dinormalisasi di pipeline. Kolom tsv_norm GENERATED agar
-- konsisten. CATATAN NULL: `coalesce(content_norm,'')` membuat tsv_norm
-- KOSONG-tapi-non-null untuk baris lama — karena itu query memakai OR dua
-- cabang (norm+tsv_norm ATAU mentah+tsv), BUKAN COALESCE (temuan F3:
-- COALESCE memilih vektor kosong → baris lama tak pernah cocok).
-- Hasil: tanpa backfill, nol regresi perilaku warisan.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_norm TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_tsv_norm TSVECTOR
  GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content_norm, '')))
  STORED;
CREATE INDEX IF NOT EXISTS idx_chunks_tsv_norm ON chunks
  USING GIN (content_tsv_norm);
