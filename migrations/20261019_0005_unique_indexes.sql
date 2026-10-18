-- Expand-only migration: unique indexes that back every ON CONFLICT target and
-- the natural keys the services rely on. Relates to REQ-DAT-001, REQ-DAT-005,
-- REQ-DAT-007, services/ingest/app/budget.py and services/core/app/companies.py.
--
-- Every ON CONFLICT target found in services/ and libs/ is already backed by a
-- primary key or unique constraint in earlier migrations (checked by
-- tests/unit/test_unique_indexes.py), so only the keys below are added.
-- Each unique index is preceded by a DO block that fails with a readable list of
-- duplicate keys instead of deleting data; clean the duplicates by hand and rerun.

-- 1. ingest.failed_fetch: at most one open (not dead lettered) failure per
--    (source, external_id). budget.py looks the row up with
--    "external_id IS NOT DISTINCT FROM", so NULL external ids share one key.
DO $$
DECLARE
    duplicates text;
BEGIN
    SELECT string_agg(format('(%s, %s) x%s', source, external_id, copies), '; ')
      INTO duplicates
      FROM (
          SELECT source, COALESCE(external_id, '') AS external_id, count(*) AS copies
            FROM ingest.failed_fetch
           WHERE dead_lettered_at IS NULL
           GROUP BY source, COALESCE(external_id, '')
          HAVING count(*) > 1
           LIMIT 20
      ) AS d;
    IF duplicates IS NOT NULL THEN
        RAISE EXCEPTION 'ux_failed_fetch_open_key blocked by duplicate open failures: %', duplicates;
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_failed_fetch_open_key
    ON ingest.failed_fetch (source, COALESCE(external_id, ''))
    WHERE dead_lettered_at IS NULL;

-- 2. core.company: one company per tenant and normalized NIT.
DO $$
DECLARE
    duplicates text;
BEGIN
    SELECT string_agg(format('(%s, %s) x%s', tenant_id, nit, copies), '; ')
      INTO duplicates
      FROM (
          SELECT tenant_id, nit, count(*) AS copies
            FROM core.company
           WHERE nit IS NOT NULL
           GROUP BY tenant_id, nit
          HAVING count(*) > 1
           LIMIT 20
      ) AS d;
    IF duplicates IS NOT NULL THEN
        RAISE EXCEPTION 'ux_company_tenant_nit blocked by duplicate NITs: %', duplicates;
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_company_tenant_nit
    ON core.company (tenant_id, nit)
    WHERE nit IS NOT NULL;

-- 3. core.project: CoreRepository.find_project_for_lead filters by lead_id
--    alone, so the (tenant_id, lead_id, updated_at, id) index does not serve it.
CREATE INDEX IF NOT EXISTS ix_project_lead_id
    ON core.project (lead_id);
