-- T-181 (D-26, REQ-LNS-015): core.lead.project_id, filled when a lead is converted to a project.
-- EXPAND only (INV-06). The column is nullable with no default, so ADD COLUMN only changes the
-- catalog and does not rewrite the table. Existing rows keep NULL (no backfill guess); the
-- per-project funnel is empty for them until a conversion fills the column. The column
-- inherits the row level security of core.lead. Same schema only (INV-01). Safe to run twice.

ALTER TABLE core.lead ADD COLUMN IF NOT EXISTS project_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'lead_project_id_fkey' AND conrelid = 'core.lead'::regclass
    ) THEN
        ALTER TABLE core.lead
            ADD CONSTRAINT lead_project_id_fkey FOREIGN KEY (project_id)
            REFERENCES core.project(id) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_lead_tenant_project
    ON core.lead (tenant_id, project_id)
    WHERE project_id IS NOT NULL;
