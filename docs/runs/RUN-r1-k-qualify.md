# Notes r1-k T-168: qualification applies the tenant of the score event

## What changed
- `services/core/app/qualification.py`: `QualificationTransaction` gains `set_tenant_context(tenant_id)`.
  `handle_score_event` reads `tenant_id` from the event and calls it first, before the inbox
  insert, `get_company_tenant`, `lock_key`, `find_leads` and every write.
- `services/core/app/repository.py`: `SqlQualificationTransaction.set_tenant_context` calls
  `libs.common.tenant.set_tenant_context` on the transaction connection.
- `worker.py` unchanged (the event already carries the tenant).
- Tests: fakes in `test_auto_qualification.py` (now RLS-like), `test_hot_alert.py`,
  `support_pipeline.py`; `test_cov_infra.py` qualification case; two new real-Postgres cases in
  `tests/integration/test_sql_r1_flows.py` (create, advance, redelivery no-op; other tenant: zero rows).

## Commands and results
- Acceptance pytest list (with a local PostgreSQL 16 on a temp cluster, `TEST_DATABASE_URL` set):
  128 passed, 1 xfailed. `test_sql_r1_flows.py`: 19 passed.
- Mutation check: with the `set_tenant_context` call disabled, the create/advance integration test fails.
- `make static`: OK. `make test`: 1578 passed, 2 skipped, 4 xfailed.

## Assumptions
- An event without a (valid) tenant keeps today's behavior: no context is set, so it only sees
  companies without tenant. A tenant-owned company answers `unknown_company`.
- An event of another tenant answers `unknown_company` and writes no lead; the inbox row is
  still written, as before for unknown companies.

## Expected first CI failure
`tests/integration/test_sql_r1_flows.py::test_score_event_creates_then_advances_the_lead_of_the_event_tenant`
if the tenant context is not applied (or if `core.processed_event` RLS rejects the insert).

## Coordinator verification
Tasks: T-168 done. Re-ran `make static` (ok) and `make test` (1578 passed, 2 skipped, 4 xfailed). Integration tests ran on a temporary local PostgreSQL 16 (19 passed in test_sql_r1_flows.py). No Docker, no migrations.
