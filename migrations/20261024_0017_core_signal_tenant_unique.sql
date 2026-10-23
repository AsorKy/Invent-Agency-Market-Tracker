-- Expand: per-tenant unique key on core.signal (T-195, D-37 option B, REQ-DAT-005, INV-06).
--
-- The global UNIQUE (source, source_external_id) stays until T-196 drops it in its own pull
-- request. This adds UNIQUE (tenant_id, source, source_external_id) beside it so create_signal can
-- use ON CONFLICT on the tenant key. Re-runnable. It first checks that no existing row violates
-- the new key and fails with an explicit message; it never deduplicates by deleting.

DO $$
DECLARE
    duplicates bigint;
BEGIN
    SELECT count(*) INTO duplicates FROM (
        SELECT 1 FROM core.signal
        WHERE source_external_id IS NOT NULL
        GROUP BY tenant_id, source, source_external_id
        HAVING count(*) > 1
    ) d;
    IF duplicates > 0 THEN
        RAISE EXCEPTION 'core.signal has % duplicate (tenant_id, source, source_external_id) groups; resolve them manually before this migration (no automatic deletion)', duplicates;
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS signal_tenant_source_external_id_key
    ON core.signal (tenant_id, source, source_external_id);
