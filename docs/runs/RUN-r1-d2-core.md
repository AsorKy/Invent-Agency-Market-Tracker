# Run report r1-d2-core

Branch: `agent/r1-d2-core`

## Outcome
- T-084: done (coordinator). Removed `analytics_url` from settings, snapshot and `.env.example`; added `RUES_DELTA_URL`, `OPS_ALERT_FROM`, `OPS_ALERT_TO` (APP_PROFILE, FRONTEND_ORIGIN, IDENTITY_VALIDATION_ENABLED already existed).
- T-085: done (lead-backend-engineer). New `services/core/app/brief_facts.py`, route `GET /internal/core/companies/{id}/brief-facts`, kanban `as_of` now current UTC time, contract test in `tests/unit/test_brief_facts.py`.

This report replaces an earlier stale one on this branch that said the tasks were missing from TASKS.md; they exist now.

## Commands
`make static` and `make test` pass before each commit; `test_sql_consistency.py` passes.

## Assumptions and risks
- Migrations lack RUES status, quarantine, certificate and enrichment columns, so those brief facts are read from company `source_flags` JSON (nothing writes those keys yet); `rues_status` defaults to matched/pending from `nit`. `overdue_tasks` is always 0.
- `as_of` is "now"; the spec does not define it more precisely.
- `tests/unit/test_openapi.py` got one NOT_PUBLIC line (outside write_scope, needed for the route).
- n8n `wf_socioeco_refresh.json` and its README still reference `$env.ANALYTICS_URL`, now absent from `.env.example`; follow-up needed.

## Changed files
libs/common/settings.py, .env.example, tests/unit/snapshots/settings_fields.json, services/core/app/{brief_facts,repository,main}.py, tests/unit/test_brief_facts.py, tests/unit/test_openapi.py, TASKS.md, this report.
