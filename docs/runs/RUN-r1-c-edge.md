# Run report r1-c-edge

Branch: `agent/r1-c-edge`. Tasks: T-030, T-031, T-032 (all done, one commit each).

## Commands
- venv python3.12, `pip install '.[dev,streamlit]'`: ok.
- T-030: `pytest -q tests/unit/test_identity_rbac.py` -> 7 passed.
- T-031: `pytest -q tests/unit/test_identity_session.py` -> 9 passed.
- T-032: four gateway/tenant/profile test files -> 71 passed.
- After each task: `make static` clean; `make test` final 628 passed, 2 skipped, 4 xfailed.

## Changed files
- services/identity/ (Django project, iam app, sessions, dev-user command), pyproject.toml (identity extra), services/gateway/app/ (routes, auth, readiness, main), new tests for the four tasks, TASKS.md (own blocks).
- Outside write_scope: `tests/unit/test_gateway.py` adjusted to the new gateway behavior (analytics removed, /me route, csrf cookie name, 401 without credentials); needed for `make test`.

## Assumptions
- L2/L3 resource names for `not_in_layer` guessed from ADR-0002/0003 topics.
- Out-of-scope city/service-line filters for intake/bd_rep return 404.

## Unresolved risks / follow-ups
- `analytics_url` still in `libs/common/settings.py` and `tests/unit/snapshots/settings_fields.json` (outside scope; remove both together).
- `identity.credential` and `identity.auth_session` come from Django migration `iam.0001`, not SQL migrations; deploy needs `django-admin migrate` and CREATE on the identity schema. Database engineer may move them to SQL.
- `procs.toml` still starts the FastAPI bootstrap, not Django; switch to gunicorn when compose is ready. Two JWKS endpoints must not run together.
- Admin registers only Tenant and UserAccount (composite PKs); staff login needs auth/sessions tables.
- No login throttling in identity; rely on gateway rate limiter.
- One identity call per cookie request (no token cache); core must filter lists by `X-User-Scopes`.
- `IDENTITY_VALIDATION_ENABLED` is off by default; `.env.example`/compose need `APP_PROFILE`, `FRONTEND_ORIGIN`, `IDENTITY_VALIDATION_ENABLED`.
- Per-file mypy directives for Django; a `[[tool.mypy.overrides]]` for django/rest_framework would be cleaner.
- Not a conflict between sources, none recorded. No workflow-file pushes were rejected.
