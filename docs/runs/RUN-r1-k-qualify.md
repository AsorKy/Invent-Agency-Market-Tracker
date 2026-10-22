# Run r1-k-qualify

## Tasks
- T-168: done. `handle_score_event` reads `tenant_id` from the event and calls the new `QualificationTransaction.set_tenant` before the first read or write (inbox insert included). `SqlQualificationTransaction.set_tenant` calls `set_tenant_context`. Fakes in `support_pipeline.py`, `test_auto_qualification.py`, `test_hot_alert.py` got the method. `worker.py` needed no change (the handler already receives all fields).

## Commands
- Setup: python3.12 venv, `pip install '.[dev,streamlit,identity,sqltest]'`: ok.
- Local PostgreSQL 16 started as user `postgres` (port 5544), `TEST_DATABASE_URL` set.
- Acceptance pytest command of T-168: all passed (real Postgres cases included).
- With the fix disabled, 2 integration tests failed (create/advance, second delivery), proving the test catches the defect.
- `make static`: ok. `make test`: 1579 passed, 2 skipped, 4 xfailed.

## Tests added
- Integration (core_app, `build_handlers` + `SqlQualificationUnitOfWork`): create lead (tenant, company, stage discovered, version 1), advance to hot (version 3), duplicate delivery changes nothing, event with another tenant creates zero lead rows.
- Unit: tenant context is set first, no context without tenant, other-tenant event writes no lead, repository `set_tenant` SQL.

## Assumptions
- An event without `tenant_id` keeps today's behavior: no context is set, so under RLS it only sees companies without tenant (a tenant company answers `unknown_company`).
- An event with another tenant's id answers `unknown_company`; the inbox row (`core.processed_event`, no tenant column) is still recorded, as before for unknown companies. No lead, audit or outbox rows are written. A defensive `tenant_id != event tenant` check was added beside RLS.

## Risks
- If CI fails first, expect `test_score_event_creates_then_advances_the_lead_of_a_tenant_company` (depends on default thresholds hot 70 and qualified, score 10 stays discovered).
- Events published without `tenant_id` for tenant companies still produce no lead; the producer must carry it (T-150).
