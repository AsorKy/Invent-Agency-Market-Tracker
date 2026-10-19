# Run report r1-d2-data

Branch: `agent/r1-d2-data`. Tasks: T-083, T-086, T-087, T-122, T-081 (run in this order, one at a time).

## Done
- T-083: `migrations/20261019_0006_identity_session_tables.sql`, `tests/unit/test_identity_schema.py`. Snapshot `table_catalog.json` refreshed.
- T-086: `migrations/20261019_0007_brief_config_version.sql` (adds `brief.record_digest.config_version`), `tests/unit/test_brief_schema.py`. No new tables: existing `brief.record_digest` covers the cache; rules/config_version come from core.
- T-087: `SqlBriefRepository` and `BriefStore` protocol in `services/brief/app/repository.py`; `tenant_id` added to repository methods and service regenerate path; `create_repository(database_url)` used in `main.py`.
- T-122: lead route resolves lead to company via `CoreSource.get_lead_company` (D-2 fixed, xfail removed); optional `HttpSummarizer` (`BRIEF_SUMMARIZER_URL`, `BRIEF_SUMMARIZER_TIMEOUT_SECONDS`, brief-local env); missing tenant header returns 400.
- T-081: collector follows buffered `(sql, params)` / `.append` literals in the same class; `UNRESOLVED_SQL` emptied; no new findings (5 buffered statements in `to_core.py`, all consistent).

## Blocked
None.

## Commands
`make static` and `make test` run before every commit: all pass (final: 916 passed, 2 skipped, 7 xfailed). `python ops/check_migrations.py` OK.

## Assumptions and deviations
- Remote branch `agent/r1-d2-data` already held a stale run report commit (tasks "not found"); I merged it (no force push) and replaced the report.
- Edited outside write_scope, as needed: `tests/unit/snapshots/table_catalog.json` (T-083, T-086, via UPDATE_SNAPSHOTS), `tests/unit/support_brief.py` and `tests/unit/support_pipeline.py` (added `get_lead_company` to fakes, T-122).

## Unresolved risks
- `identity.auth_session` RLS policy is permissive when `app.tenant_id` is unset (login looks up by key hash before tenant is known); belongs to the T-082 decision.
- Django `iam.0001` and the new SQL both define the identity tables; models lack `managed = IDENTITY_MANAGED_TABLES`. Not changed (outside scope).
- Nothing tested against real Postgres (no database in sandbox); SQL checked by pglast and fakes only. T-095 integration test should cover `SqlBriefRepository`.
- Core has no `GET /internal/core/leads/{id}` guarantee for `company_id` and no brief-facts route (D-3, T-085).
- Add `BRIEF_SUMMARIZER_URL` and `BRIEF_SUMMARIZER_TIMEOUT_SECONDS` to `.env.example` (T-084 owner). The summarizer request/response contract is a new design, undocumented elsewhere.
- Class-scoped buffered-SQL heuristic in the collector can over-check but not under-check.
