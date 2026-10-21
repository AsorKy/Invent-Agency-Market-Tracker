# Run report r1-d2-ingest

Branch: `agent/r1-d2-ingest`. A stale report from an earlier session was on the branch ("tasks not found"); this report replaces it.

## Tasks
| Task | Status | Evidence |
| --- | --- | --- |
| T-120 | done | ingest_to_core and pipeline_flow tests: 20 passed; D-1 xfail and shim removed |
| T-098 | done | test_mocks_sources: 16 passed |
| T-089 | done | 19 passed on the 3 acceptance files; test_sql_consistency passes |
None blocked.

## Commands
Setup: python3.12 venv at /tmp/venv, `pip install '.[dev,streamlit,identity]'` succeeded. Before each commit `make static` and `make test` passed (final: 916 passed, 2 skipped, 7 xfailed).

## Changed files
- T-120: services/ingest/app/{to_core,processor,tenant(new)}.py, tests/unit/{test_ingest_to_core,test_pipeline_flow,support_pipeline}.py, ops/seed/loader.py (DEMO_TENANT_ID; needed so make static passes).
- T-098: services/mocks/app/main.py, tests/unit/test_mocks_sources.py.
- T-089: services/ingest/app/{to_core,repository,processor,rues_queue(new)}.py, tests/unit/{test_ingest_outside_tx,test_rues_queue}.py.
- TASKS.md (own blocks only).

## Assumptions
- Tenant comes from new env var `INGEST_TENANT_ID` (ingest-local setting), never the payload.
- Edits to tests/unit/support_pipeline.py and ops/seed/loader.py were slightly outside write_scope but required by the task (shim removal, static gate).

## Unresolved risks
1. `INGEST_TENANT_ID` must be added to `.env.example` and docker-compose for ingest-processor (coordinator-owned, not done); the processor refuses to start without it.
2. No migration/seed inserts a tenant row for DEMO_TENANT_ID; check for an FK to identity.tenant.
3. Core ignores the `Idempotency-Key` header and `SignalCreate` has no tenant_id (extra key ignored); lead-backend scope.
4. RUES queue worker sends only the company upsert; a `retry_later` record's signal is never sent later. It does not claim rows, so run a single instance. No "delivered" candidate status.
5. Remaining strict xfails in the pipeline test: D-2, D-3, D-4 (T-121).
6. `ops/seed/loader.py` calls private `HttpCoreClient._post`.
