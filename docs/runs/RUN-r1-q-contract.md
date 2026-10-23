# Run r1-q-contract

Do not apply until it is confirmed that 20261024_0017 ran in the target environment; if in doubt, wait.

## Tasks
- T-196 done (contract only, own pull request).

## What changed
- `migrations/contract_20261024_0018_drop_signal_global_unique.sql`: the global key was declared inline in `20261017_0001` as `UNIQUE (source, source_external_id)` on `core.signal`, so its real name is `signal_source_source_external_id_key`. The migration does not hardcode it: it finds the `u` constraint whose columns are exactly `(source, source_external_id)` and drops only that. It refuses (raises, drops nothing) when `signal_tenant_source_external_id_key` from 0017 is missing. Re-runnable. The warning line is at its top.
- `tests/integration/test_sql_r1_flows.py`: `test_signal_global_unique_key_is_dropped` (no unique constraint left, tenant index present) and `test_two_tenants_can_use_the_same_signal_external_id` (same source and external id in two tenants: no raise, own row each, idempotent per tenant).

## Commands (venv python3.12, `.[dev,streamlit,identity,sqltest]` installed)
- `python ops/check_migrations.py --base origin/main`: OK
- `pytest -q tests/unit/test_sql_consistency.py`: 25 passed
- `make static`: ok
- `make test`: 1739 passed, 2 skipped, 4 xfailed
- Integration cases NOT run locally (no Docker/Postgres); the CI integration stage is the real check.

## Assumptions
- No later migration re-adds or renames the global key (grep found none).
- New cases use the same fixtures and tenant-context path as the T-195 cases; the two-tenant case fails before 0018 because create_signal would raise.

## Unresolved risks
- Applying 0018 before 0017 is blocked by the migration guard, but the operator must still confirm 0017 in the target environment.
- Migration unproven on real Postgres until CI integration runs.
