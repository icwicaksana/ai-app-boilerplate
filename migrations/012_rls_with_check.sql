-- 012_rls_with_check.sql (v0.8 F1): perbaiki policy 007 yang hanya USING.
--
-- Temuan expert DB: policy `tenant_isolation` di 007 hanya USING (berlaku
-- SELECT/UPDATE/DELETE) — INSERT lolos RLS. Tambahkan WITH CHECK yang sama.
-- Idempotent, tanpa backfill. (approvals/audit_log tidak punya RLS sama
-- sekali — di luar scope; memperkenalkan RLS di sana = perubahan perilaku
-- auth/HITL, bukan v0.8.)
ALTER POLICY tenant_isolation ON documents
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
ALTER POLICY tenant_isolation ON ingestion_jobs
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
ALTER POLICY tenant_isolation ON chunks
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
ALTER POLICY tenant_isolation ON source_connections
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
ALTER POLICY tenant_isolation ON source_watermarks
  USING (tenant = current_setting('app.current_tenant', true))
  WITH CHECK (tenant = current_setting('app.current_tenant', true));
