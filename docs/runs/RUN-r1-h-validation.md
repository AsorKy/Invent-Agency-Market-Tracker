# RUN r1-h-validation

## Tasks
- T-146: blocked. `core.project.status` is unconstrained text (default `'draft'`, migrations/20261017_0001_expand_initial_schemas.sql:178); only `'draft'` is written (services/core/app/actions.py:251, 272). No closed value exists. The task forbids inventing one or a migration, so no code was written.
- T-147: blocked (depends on T-146).
- T-148: blocked (depends on T-146, T-147).

## Commands
- python3.12 venv and `pip install '.[dev,streamlit,identity,sqltest]'`: OK.
- No make static / make test run; no code changed (only TASKS.md and this report).

## Assumptions
None beyond the task text.

## Unresolved risks / decision needed
Owner must choose: (a) allow a plain-text `'killed'` status (no migration needed), (b) add a migration, or (c) ship the funnel routes without the status change. Then T-146, T-147, T-148 can run. Planned T-146 routes: GET /internal/core/funnel/projects/{id} and POST .../decision, gateway-only.
