-- Expand-only migration: brief.record_digest.config_version (REQ-BRF-001, REQ-DAT-001).
-- Mirrors StoredDigest.config_version in services/brief/app/models.py: the tenant
-- configuration version used to generate the digest. A value lower than the current
-- core.tenant_config version marks the digest stale (lazy invalidation).
--
-- The in-memory repository keeps only the latest digest per company, which the
-- existing brief.record_digest (PK company_id, source_version) and ix_brief_latest
-- already serve; rules and versions are read from core, so no other table is added.
-- Existing rows get 0, the model default. Row level security on brief.record_digest
-- is already enabled and forced with a tenant_id policy by migration 0002.

ALTER TABLE brief.record_digest
    ADD COLUMN IF NOT EXISTS config_version bigint NOT NULL DEFAULT 0
        CHECK (config_version >= 0);
