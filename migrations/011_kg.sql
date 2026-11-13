-- 011_kg.sql (v0.8 F1): Knowledge Graph PG-native, doc-scoped (D1/D2).
--
-- Pola 007: tanpa CREATE INDEX CONCURRENTLY (tabel baru kosong; migrasi
-- berjalan dalam 1 blok transaksi), advisory lock + schema_migrations via
-- app/infra/rag_migrate.py, RLS FORCED + USING dan WITH CHECK, GRANT app_api.
-- Acuan traversal: frontier BFS iteratif bounded (D3), BUKAN recursive CTE.
-- norm = lower+fold TANPA stem (invariant tulis=baca, pola RAG_STEMMER).

-- 1) ENTITAS doc-scoped: hapus = DELETE per-doc, tanpa refcount/GC (D2).
CREATE TABLE kg_entities (
  tenant TEXT NOT NULL,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  norm TEXT NOT NULL CHECK (char_length(norm) BETWEEN 1 AND 256),
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 512),
  type TEXT NOT NULL CHECK (char_length(type) BETWEEN 1 AND 64),
  properties JSONB NOT NULL DEFAULT '{}'::jsonb,
  onto_version TEXT NOT NULL DEFAULT 'kg_onto/v1',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, norm)
);

-- 2) EDGE: PK ramping (id global-unik); dedup via UNIQUE terpisah.
CREATE TABLE kg_edges (
  id BIGINT GENERATED ALWAYS AS IDENTITY,
  tenant TEXT NOT NULL,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  src_norm TEXT NOT NULL,
  dst_norm TEXT NOT NULL,
  relation TEXT NOT NULL CHECK (char_length(relation) BETWEEN 1 AND 64),
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  layer TEXT NOT NULL DEFAULT 'L2' CHECK (layer IN ('L1', 'L2', 'L3')),
  coref_resolved BOOLEAN NOT NULL DEFAULT TRUE,
  access_level TEXT NOT NULL DEFAULT 'public',
  chunk_id TEXT NOT NULL,
  chunk_row_id UUID REFERENCES chunks(id) ON DELETE CASCADE,
  chunk_ord INT NOT NULL,
  properties JSONB NOT NULL DEFAULT '{}'::jsonb,
  onto_version TEXT NOT NULL DEFAULT 'kg_onto/v1',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  CONSTRAINT chk_edges_not_self CHECK (src_norm <> dst_norm),
  UNIQUE (document_id, src_norm, dst_norm, relation)
);

-- 3) Index: seed lookup + BFS fwd/bwd covering (index-only scan) + cleanup.
CREATE INDEX idx_ent_doc ON kg_entities (tenant, document_id);
CREATE INDEX idx_ent_norm ON kg_entities (tenant, norm)
  INCLUDE (name, type, document_id);
CREATE INDEX idx_edge_seed ON kg_edges (tenant, src_norm)
  INCLUDE (dst_norm, relation, confidence, layer, onto_version,
           access_level, document_id, chunk_id, chunk_row_id, chunk_ord);
CREATE INDEX idx_edge_bwd ON kg_edges (tenant, dst_norm)
  INCLUDE (src_norm, relation, confidence, layer, onto_version,
           access_level, document_id, chunk_id, chunk_row_id, chunk_ord);

-- 4) Autovacuum agresif-ringan (pola rebuild: INSERT+DELETE, minim UPDATE).
ALTER TABLE kg_entities SET (
  autovacuum_vacuum_scale_factor = 0.02,
  autovacuum_vacuum_threshold = 50,
  autovacuum_analyze_scale_factor = 0.025,
  autovacuum_analyze_threshold = 50,
  autovacuum_vacuum_insert_scale_factor = 0.05,
  autovacuum_vacuum_insert_threshold = 200,
  autovacuum_vacuum_cost_delay = 0
);
ALTER TABLE kg_edges SET (
  autovacuum_vacuum_scale_factor = 0.02,
  autovacuum_vacuum_threshold = 50,
  autovacuum_analyze_scale_factor = 0.025,
  autovacuum_analyze_threshold = 50,
  autovacuum_vacuum_insert_scale_factor = 0.05,
  autovacuum_vacuum_insert_threshold = 200,
  autovacuum_vacuum_cost_delay = 0
);

-- 5) RLS FORCED + USING dan WITH CHECK + GRANT.
ALTER TABLE kg_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_entities FORCE ROW LEVEL SECURITY;
ALTER TABLE kg_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_edges FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kg_entities
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
CREATE POLICY tenant_isolation ON kg_edges
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
GRANT SELECT, INSERT, UPDATE, DELETE ON kg_entities, kg_edges TO app_api;
GRANT USAGE, SELECT ON SEQUENCE kg_edges_id_seq TO app_api;

-- 6) Stage worker baru: documents.status CHECK belum kenal 'graph'/'dead'.
ALTER TABLE documents DROP CONSTRAINT documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check CHECK (
  status IN ('pending', 'parsing', 'chunking', 'embedding', 'indexing',
             'graph', 'ready', 'failed', 'dead', 'deleted'));
