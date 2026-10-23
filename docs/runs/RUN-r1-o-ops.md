# Run report r1-o-ops

Branch: agent/r1-o-ops. Tasks: T-192, T-193 (both done).

## Tasks done
- T-192: `python ops/doctor.py --tenants` (read only, counts only, same superuser psql path as `--pipeline`). Eight counts: NULL `tenant_id` in core.company, lead, signal, location, lead_outcome; unresolved rows (NULL tenant whose company or lead has no tenant or is missing) for signal, location, lead_outcome. Exit 1 when any count is above zero or a query fails. Runbook: new step 3 in section 2 (there is no section named "migration"; section 2 is where the first migrate happens).
- T-193: `ops/verify_live.py` refuses with exit 2 when the effective role (from `VERIFY_LIVE_DATABASE_URL`, else `POSTGRES_USER`) ends in `_app`; the message prints no URL, role or password. New compose test for the verify-live URL, new verify_live tests, one paragraph in runbook section 7.

## Commands
- venv python3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: ok (slow).
- `pytest -q tests/unit/test_doctor.py tests/unit/test_runbook.py tests/unit/test_verify_live.py tests/unit/test_compose.py`: pass.
- `python ops/check_language.py`: OK. `make static`: pass. `make test`: 1727 passed, 2 skipped, 4 xfailed.

## Assumptions
- Unresolved count also includes orphans (parent row missing), since migration 0014 would raise for them too.
- No SQL added to services or migrations, so test_sql_consistency and test_openapi were not affected (both ran inside make test).

## Risks
- `--tenants` is not run against a real Postgres (no Docker); the SQL is simple counts but is unproven in CI.
- The `_app` check looks at the role name only; a differently named restricted role is not detected.
