-- Expand: core.company stores the latest score it received (T-145, REQ-COR-001, REQ-DAT-001).
--
-- core_app has no grant on the ml schema (INV-01), so core keeps its own copy of the score
-- that the lead.score_updated event carries. score is nullable (0 to 100) until the next
-- score event fills it; no backfill. score_source_version is the producer source_version text
-- (company.version + sum of signal versions, a non-negative integer as text); the worker
-- never overwrites a numerically newer value. The columns inherit the row level security
-- of core.company (company_tenant_policy), so no new policy is needed. Additive only.

ALTER TABLE core.company ADD COLUMN IF NOT EXISTS score integer;
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS score_source_version text;
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS score_updated_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'company_score_range' AND conrelid = 'core.company'::regclass
    ) THEN
        ALTER TABLE core.company
            ADD CONSTRAINT company_score_range CHECK (score IS NULL OR score BETWEEN 0 AND 100);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_company_tenant_score
    ON core.company (tenant_id, score)
    WHERE score IS NOT NULL;
