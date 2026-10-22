# RUN r1-g-pulse

## Tasks
- T-135 done: `services/pulse/app/lens.py` router (ingest profiles/communities/topics, `GET /internal/pulse/projects/metrics`, `POST /internal/pulse/socioeconomic/refresh`); migration 0010 (five pulse tables, forced RLS); table catalog snapshot regenerated.
- T-136 done: migration 0011 `pulse.attribution`; `POST /internal/pulse/ingest/attribution`; `publish_attribution` in `services/ml/app/attribution_service.py`.

## Commands
- venv py3.12 + `pip install '.[dev,streamlit,identity,sqltest]'`: ok.
- `pytest -q tests/unit/test_pulse_lens_routes.py`: 8 passed.
- `pytest -q tests/unit/test_attribution_storage.py tests/unit/test_sql_consistency.py`: 30 passed.
- `python ops/check_migrations.py`: OK. `make openapi`: no diff (export covers gateway only).
- `make static` and `make test`: pass before each commit (final: 1304 passed, 2 skipped, 4 xfailed).

## Assumptions
- Ingest routes accept only the `ml` service token; tenant from `X-Tenant-Id` or body `tenant_id`.
- Refresh and metrics routes use `require_internal_token` (any valid signed token); n8n credential service name unknown.
- pulse_app grants rely on default privileges from `ops/bootstrap_roles.py` (not run against a DB).
- Floats stored as numeric (taxonomy tests ban double).

## Unresolved risks
- `services/ml/app/publisher.py` sends no tenant, so profiles/communities/topics ingest answers 400 `tenant_required` until it sends `X-Tenant-Id` or a body `tenant_id` (outside both write scopes; follow-up).
- `pulse.project_snapshot` / `project_stage_visit` have no writer yet; the metrics route returns empty values.
- SQL not exercised against real Postgres (no Docker); the CI e2e stage is the first real check.
- n8n tokens live 60 s; the static-header credential of wf_socioeco_refresh is its own issue.
