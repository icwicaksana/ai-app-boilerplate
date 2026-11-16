-- 013_kg_alias.sql (v0.8 F4): alias seed per-tenant (stratum alias/sinonim).
--
-- Pola 011: RLS FORCED + USING dan WITH CHECK + GRANT app_api, tanpa
-- CONCURRENTLY. Canonical = norm entity tujuan (dock ke kg_entities.norm).
CREATE TABLE kg_aliases (
  tenant TEXT NOT NULL,
  alias_norm TEXT NOT NULL CHECK (char_length(alias_norm) BETWEEN 1 AND 256),
  canonical_norm TEXT NOT NULL
    CHECK (char_length(canonical_norm) BETWEEN 1 AND 256),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, alias_norm),
  CONSTRAINT chk_alias_not_self CHECK (alias_norm <> canonical_norm)
);
ALTER TABLE kg_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_aliases FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kg_aliases
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
GRANT SELECT, INSERT, UPDATE, DELETE ON kg_aliases TO app_api;
