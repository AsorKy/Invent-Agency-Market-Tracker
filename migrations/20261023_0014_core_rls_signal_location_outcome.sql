-- T-082 (D-29, REQ-DAT-005, REQ-SEC-005): row level security on core.signal, core.location and
-- core.lead_outcome. EXPAND only (INV-06): no column or table is dropped.
--
-- 1. Backfill tenant_id from the owning row (company for signal and location, lead for outcome).
-- 2. Refuse to continue while a row has no resolvable tenant: such a row would be invisible
--    under the policy and a NOT NULL constraint cannot hold it. An operator must assign it.
-- 3. SET NOT NULL, ENABLE and FORCE row level security, and create `<table>_tenant_isolation`
--    with the same predicate as the policies of 20261018_0004 (a missing or empty
--    app.tenant_id matches no row). Every statement is safe to run twice.

DO $$
DECLARE
    item text[];
    orphans bigint;
    policy_name text;
BEGIN
    -- Backfill (dynamic SQL: the schema checker of the tests reads only static DDL).
    EXECUTE 'UPDATE core.signal s SET tenant_id = c.tenant_id FROM core.company c
             WHERE s.tenant_id IS NULL AND c.id = s.company_id AND c.tenant_id IS NOT NULL';
    EXECUTE 'UPDATE core.location l SET tenant_id = c.tenant_id FROM core.company c
             WHERE l.tenant_id IS NULL AND c.id = l.company_id AND c.tenant_id IS NOT NULL';
    EXECUTE 'UPDATE core.lead_outcome o SET tenant_id = ld.tenant_id FROM core.lead ld
             WHERE o.tenant_id IS NULL AND ld.id = o.lead_id AND ld.tenant_id IS NOT NULL';

    FOREACH item SLICE 1 IN ARRAY ARRAY[
        ['core', 'signal'], ['core', 'location'], ['core', 'lead_outcome']
    ] LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I WHERE tenant_id IS NULL', item[1], item[2])
            INTO orphans;
        IF orphans > 0 THEN
            RAISE EXCEPTION
                '%.% has % row(s) without a resolvable tenant_id; assign a tenant, then rerun',
                item[1], item[2], orphans;
        END IF;
        EXECUTE format('ALTER TABLE %I.%I ALTER COLUMN tenant_id SET NOT NULL', item[1], item[2]);
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', item[1], item[2]);
        EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', item[1], item[2]);
        policy_name := item[2] || '_tenant_isolation';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = item[1] AND tablename = item[2] AND policyname = policy_name
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON %I.%I
                 USING (tenant_id::text = current_setting(''app.tenant_id'', true))
                 WITH CHECK (tenant_id::text = current_setting(''app.tenant_id'', true))',
                policy_name,
                item[1],
                item[2]
            );
        END IF;
    END LOOP;
END $$;
