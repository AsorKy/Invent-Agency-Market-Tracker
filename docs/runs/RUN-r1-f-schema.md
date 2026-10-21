# Run report r1-f-schema

Branch `agent/r1-f-schema`. Tasks: T-129 done, T-107 done.

## T-129 Retire the legacy analytics schema
- Added `migrations/contract_20261020_0008_retire_analytics.sql`: copies `analytics.fact_signal`, `fact_company_snapshot`, `fact_lead_outcome` and `semantic_metric_definition` into `pulse`, then `DROP SCHEMA IF EXISTS analytics CASCADE`.
- The legacy tables have no `tenant_id`; the tenant is taken from `core.company` or `core.lead`. Rows whose owner has no tenant are not copied. `daily_rollup` (no tenant, pulse key differs), `territory_index` and `processed_event` have no usable equivalent and are dropped.
- Tests: `test_schema_catalog.py` (analytics dropped only by a contract, copy before drop; the strict xfail for "never created" was removed because it would now XPASS), `test_sql_consistency.py` (contract files at the tail, not only one; analytics gone, pulse present; both outside the write_scope, minimal change needed by a second contract file), `snapshots/table_catalog.json` regenerated (schemas list loses `analytics`).
- **Conflict, not followed:** the task says to remove `analytics` from `LEGACY_SCHEMAS`. Baseline 0001 still contains `CREATE SCHEMA analytics`, and the guardian then rejects that checksummed file (checked: `check_migrations.py` fails). `socioeconomic` stays in the set for the same reason. `analytics` therefore stays in `LEGACY_SCHEMAS`; removal needs a new baseline (owner decision, E2.5 items 1 and 2).
- Not verified: the migration against a real Postgres (no Docker). `test_sql_consistency` parses it with pglast.

## T-107 Validation schema
- `migrations/20261020_0009_validation_schema.sql`: schema `validation`, six tables (`financial_run`, `unit_economics`, `funnel_metric`, `growth_metric`, `portfolio_snapshot`, `meta_ml_metric`), each with `source_version`, `project_id`, `measured_at` plus `tenant_id`, per-project index, forced RLS with the standard `app.tenant_id` policy, conditional grants to `core_app`.
- `ops/schemas.py`: `SHARED_ROLE_SCHEMAS = {"validation": "core"}`, `CATALOG_SCHEMAS`, `role_base()`; `owner_role`/`app_role` map `validation` to the core roles. `SCHEMAS` stays 7 role-owning schemas so the 14 roles, `BOOTSTRAP_PASSWORD_*`, Compose and the integration role tests are unchanged.
- Outside the write_scope but required: `ops/check_migrations.py` (accept `CATALOG_SCHEMAS`), `ops/bootstrap_roles.py` (create the schema owned by `core_owner`, grants to `core_app`, no new roles), `tests/unit/test_migration_guard.py` (grant check uses `role_base`).
- Interpretation: "add the schema to the single schema list" was met with `CATALOG_SCHEMAS` rather than `SCHEMAS`, because adding it to `SCHEMAS` would create `validation_owner`/`validation_app`, contradicting "operated by the core roles".

## Commands
- setup: `python3.12 -m venv /tmp/venv; pip install '.[dev,streamlit,identity,sqltest]'` ok
- `python ops/check_migrations.py`: OK (both tasks)
- `pytest -q tests/unit/test_schema_catalog.py tests/unit/test_migration_guard.py tests/unit/test_sql_consistency.py`: passed
- `make static`: passed; `make test`: 1156 passed (T-129), 1163 passed (T-107)

## Risks
- Migration numbers 0008 and 0009 may collide with other parallel sessions; rename before merge if so.
- Neither migration was run on a real database; CI e2e is the first real check. The bootstrap runs after migrate and now also creates/owns `validation`; all other defaults unchanged.
- `ruff check tests` reports pre-existing E501 in unrelated tests (not part of `make static`).
