# Run r1-j-sql

## Tasks done
- T-162 Real Postgres tests for the SQL added since r1-g: `tests/integration/test_sql_r1_flows.py` (17 tests), three helpers in `tests/integration/support_sql_pg.py`.
- T-163 Funnel decisions take a separate `SELECT id ... FOR UPDATE` on the project row (tenant context first) before the status read; 409 `project_killed` kept. Order proven lock, read, write in `tests/unit/test_funnel_routes.py`. Routes and OpenAPI unchanged.

## Commands and results
- Setup: python3.12 venv, `pip install '.[dev,streamlit,identity,sqltest]'` OK.
- A local PostgreSQL 16 was available in the sandbox (started on port 5433, `TEST_DATABASE_URL=postgresql://postgres@/postgres?host=/tmp&port=5433`), so the integration tests really ran.
- `pytest -q tests/integration/test_sql_r1_flows.py tests/integration/test_sql_postgres.py`: 23 passed.
- `pytest -q tests/unit/test_funnel_routes.py tests/unit/test_openapi.py tests/unit/test_sql_consistency.py` plus the integration files: 75 passed.
- `make static`: OK. `make test`: 1558 passed, 2 skipped, 4 xfailed.

## Assumptions
- T-162 and T-163 had disjoint write scopes, so they ran in parallel; each is committed separately, T-162 first.
- The lock sits inside `get_project` as a separate statement (no protocol change, so other fakes keep working); correct under READ COMMITTED.
- A redelivered score with equal `source_version` rewrites by design; "changes nothing" is asserted on score and version with a fixed `now`.

## Unresolved risks
- DEFECT (not fixed, outside write scope): `SqlQualificationUnitOfWork` is built without tenant context, so `get_company_tenant` reads `core.company` under RLS and `handle_score_event` returns `unknown_company` for tenant-owned companies as `core_app`; no lead is created or advanced. Fix needs the tenant from the event in `services/core/app/worker.py` and the repository. No committed test pins it.
- Likeliest first CI failure: `test_two_concurrent_kill_decisions_kill_the_project_once` (lock timing, 30 s wait), then role bootstrap if the CI Postgres user is not a superuser.
