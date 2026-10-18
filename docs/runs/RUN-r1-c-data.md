# Run r1-c-data

Branch: agent/r1-c-data. Tasks: T-093, T-094, T-095, T-097, all done. Environment: Python 3.12 venv, PyPI reachable.

| Task | Result | Evidence |
| --- | --- | --- |
| T-093 | done | `migrations/20261019_0005_unique_indexes.sql`; check_migrations OK; 4 tests pass |
| T-094 | done | 24 tests pass; 105 statements consistent with schema; `pglast` added to dev extra |
| T-095 | done | new `sqltest` extra and `make test-sql`; ran on a system PostgreSQL 16: 6 passed |
| T-097 | done | 12 marts renamed to `mart_dim_*` / `mart_fact_*`; `dbt parse` OK; 2 tests pass |

`make static` and `make test` passed before every commit (final: 571 passed, 2 skipped, 4 xfailed).

## Findings for the owner

1. Real-PostgreSQL run found a bug in my T-093 migration (GROUP BY mismatch in the duplicate check). Fixed in the T-095 commit (it touched the T-093 file).
2. Nine tables with `tenant_id` have no row level security: analytics.processed_event, core.audit_log, core.lead_outcome, core.location, core.outbox_dead_letter, core.outbox_event, core.processed_event, core.signal, ml.processed_event. The integration test lists them in `RLS_EXEMPT`; the reasons are unverified guesses. Owner decision needed, especially for core.location, core.signal and core.lead_outcome.
3. T-093 added `ix_project_lead_id` because `find_project_for_lead` filters by `lead_id` alone.
4. T-094 `DYNAMIC_SQL` has 3 justified f-string queries in services/core/app/repository.py; their interpolated fragments are not analyzed.
5. T-097: no readers of the old dbt names in services/. Docs outside transform/ (SPEC.md, run reports) may still use the old names. `dbt-postgres` was installed in the venv only for the parse; it is not in pyproject.
6. The PostgreSQL run used the sandbox's system install, not Docker. The owner should still run `make test-sql`.
