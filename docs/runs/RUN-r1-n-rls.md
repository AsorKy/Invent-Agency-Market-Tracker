# RUN r1-n-rls

Branch `agent/r1-n-rls`. Tasks: T-082 done, T-189 done, T-190 done. None blocked.

## Setup
- `python3.12 -m venv /tmp/venv` and `pip install '.[dev,streamlit,identity,sqltest]'`: OK.
- The sandbox has PostgreSQL 16 binaries, so I started a local server (port 5433, trust auth) and
  ran the integration tier with `TEST_DATABASE_URL=postgresql://postgres@localhost:5433/postgres`.
  The real-Postgres cases below were therefore executed here, not only written. The CI
  integration stage stays the reference check.

## T-082 row level security on core.signal, core.location, core.lead_outcome
- `migrations/20261023_0014_core_rls_signal_location_outcome.sql` (expand only): backfills
  `tenant_id` from the company (signal, location) or the lead (outcome), raises an exception if a
  row still has no tenant, then `SET NOT NULL`, `ENABLE` + `FORCE` RLS and policy
  `<table>_tenant_isolation`. The predicate is `tenant_id::text = current_setting('app.tenant_id', true)`
  (the pattern of 0004, so an unset context matches nothing instead of failing on a uuid cast);
  the task note wrote `::uuid`. The backfill is dynamic SQL because the schema checker of
  `test_sql_consistency.py` ignores static UPDATE statements. Re-runnable.
- Exempt registry in `tests/integration/test_sql_postgres.py` is now five tables plus the two `ml`
  outbox tables of D-29 (`core.audit_log`, `core.outbox_event`, `core.outbox_dead_letter`,
  `core.processed_event`, `ml.processed_event`, `ml.outbox_event`, `ml.outbox_dead_letter`), each with its real
  reason; the "UNVERIFIED" comment is gone; new test asserts the three tables HAVE RLS.
  `analytics.processed_event` appears only in the historical expand files and in the contract
  migration that drops it.
- Writers and readers found, and the test of each (all in `tests/integration/test_sql_r1_flows.py`,
  as `core_app`):
  1. `CoreRepository.create_signal` (writes core.signal): now inserts `tenant_id` (explicit, else
     taken from the visible company row). Test `test_create_signal_writes_the_tenant_and_needs_a_visible_company`:
     with context the row has the tenant; with no tenant and no context the insert is refused.
  2. `record_outcome` through `action_unit_of_work` (writes core.lead_outcome): the repository
     method now takes the tenant and the routes pass `X-Tenant-Id`. Bug found and fixed:
     `SqlActionUnitOfWork.__aenter__` and `SqlRetentionUnitOfWork.__aenter__` opened the
     connection directly and never called `set_tenant_context`. Test
     `test_record_outcome_runs_under_the_tenant_context` (works with context; no context or
     wrong tenant gives 404 and writes nothing).
  3. Retention `SqlRetentionTransaction.delete_company_data` (deletes signal, location,
     lead_outcome; the only app code that touches core.location). Test
     `test_retention_delete_removes_the_three_tables_only_under_the_context`.
  4. Readers of core.signal: `read_brief_facts` and `read_score_features` (already set the
     context). Test `test_brief_facts_and_score_features_count_signals_only_under_the_context`.
  5. Policy level for all three tables: `test_the_three_tables_hide_rows_without_context_and_across_tenants`
     and `test_the_three_tables_refuse_writes_without_the_matching_context`.
  - core.location has no insert path in the app; only the retention delete. Not found: imports,
    seed (the demo seed goes through the gateway) or ml/pulse code touching these tables.
- `tests/unit/test_lead_actions.py`: the fake `action_unit_of_work` takes an optional tenant (outside the
  write_scope, needed by the signature change).
- Commands: `pytest -q tests/integration/test_sql_postgres.py tests/integration/test_sql_r1_flows.py tests/unit/test_sql_consistency.py tests/unit/test_schema_catalog.py` passed (with the local Postgres);
  `python ops/check_migrations.py` OK; `pytest -q tests/integration` 44 passed, 4 skipped;
  `make static` OK; `make test` 1680 passed, 2 skipped, 4 xfailed. `table_catalog.json` needed no change.

## T-189 pulse refresh accepts only n8n_socioeco
- `services/pulse/app/lens.py`: the refresh route uses `require_service_token("n8n_socioeco")`;
  the other routes keep `require_internal_token`. New `tests/unit/test_pulse_lens.py` (the file named in the
  task did not exist): accepted for `n8n_socioeco`; 401 for gateway, seed, verify_live, ml, brief, a
  missing token and a token signed with another key. One case added to `test_internal_auth.py`.
  The refresh cases of `test_pulse_lens_routes.py` now send the `n8n_socioeco` token.
  Mint route, workflow and OpenAPI untouched; workflow not activated.
- `pytest -q tests/unit/test_pulse_lens.py tests/unit/test_internal_auth.py tests/unit/test_pulse_lens_routes.py tests/unit/test_openapi.py` passed; make static OK; make test 1689 passed, 2 skipped, 4 xfailed.

## T-190 test lint to zero
- Command: `ruff check tests` -> `All checks passed!` (14 findings before: B905, E501, F401/I001, B023).
  Fixes: `zip(..., strict=False)` (keeps behaviour for unequal lengths), line wraps by `ruff format`
  on six files or by hand, import order, and in `test_event_chain_producers.py` the unused
  `statements` list (it was never asserted) was removed instead of binding the loop variable.
  `tests/unit/test_contracts.py` was also fixed (one E501; not in the write_scope list but it
  was in the findings). `test_funnel.py`: only the flagged line. Asserts unchanged.
- Recommendation: add `ruff check tests` to `make static` (it is now clean, so it costs nothing and
  stops regression). I did not touch the Makefile or CI. `ruff format --check tests` still
  reports 6 files, unrelated to this task.

## Assumptions
- Migration refuses to run if rows have no resolvable tenant (e.g. F0 rows whose company has a NULL tenant);
  an operator assigns a tenant first. A fresh stack is unaffected.
- Pushed with `git push` to the agent branch (it worked); no workflow files touched.

## Unresolved risks
- Retention (`/compliance/opt-out`, `/compliance/purge`) is cross-tenant by nature and has no tenant
  in its request. `core.company` was already under RLS, so as `core_app` without a context both
  already see nothing; the three new tables now behave the same. Needs a decision (a per-tenant purge,
  or a tenant in the opt-out request); not changed here.
- `create_signal` with an explicit `tenant_id` that differs from the company tenant is not rejected
  (FKs bypass RLS); a second tenant ingesting the same `(source, source_external_id)` now gets an error
  instead of the other tenant's id (the unique key is global).
- `record_outcome` with no `X-Tenant-Id` header answers 404 (no context); the header is optional in the OpenAPI.
- `ops/verify_live.py` counts `core.signal` directly with its own database credentials; with a role under RLS the count is 0.
  It was already a cross-schema operator tool; confirm it uses an owner/superuser URL.
