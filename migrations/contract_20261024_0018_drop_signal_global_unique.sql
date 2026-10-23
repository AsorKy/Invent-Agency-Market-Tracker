-- Do not apply until it is confirmed that 20261024_0017 ran in the target environment; if in doubt, wait.
--
-- CONTRACT: drop the global unique key on core.signal (T-196, D-37 option B, REQ-DAT-005, INV-06).
--
-- 20261017_0001 declared the key inline as UNIQUE (source, source_external_id), so PostgreSQL
-- named it core.signal_source_source_external_id_key. The name is not assumed: the block below
-- looks up the unique constraint of core.signal whose columns are exactly (source,
-- source_external_id) and drops only that one. The per-tenant key from 0017 (index
-- signal_tenant_source_external_id_key) is kept; if it is missing the migration fails and drops
-- nothing. Re-runnable. Commit and review this file in its own pull request.

DO $$
DECLARE
    old_key text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'core' AND tablename = 'signal'
          AND indexname = 'signal_tenant_source_external_id_key'
    ) THEN
        RAISE EXCEPTION 'index core.signal_tenant_source_external_id_key is missing: 20261024_0017 has not run, refusing to drop the global unique key';
    END IF;

    FOR old_key IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'core.signal'::regclass
          AND c.contype = 'u'
          AND (
              SELECT array_agg(a.attname::text ORDER BY k.ord)
              FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
          ) = ARRAY['source', 'source_external_id']
    LOOP
        EXECUTE format('ALTER TABLE core.signal DROP CONSTRAINT %I', old_key);
    END LOOP;
END
$$;
