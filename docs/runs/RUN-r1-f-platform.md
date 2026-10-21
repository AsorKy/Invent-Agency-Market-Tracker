# RUN r1-f-platform

Branch: `agent/r1-f-platform`. Tasks: T-132, T-130, T-131 (all done, run in that order, one commit each).

## Tasks done
- **T-132**: `ops/bootstrap_roles.py` grants each owner role to the current user (`WITH SET TRUE` on PG16+, plain grant before), skipped for superusers. `--db-url` mode creates extensions one by one and reports those the platform must enable. Tests in `tests/unit/test_bootstrap_roles_privileges.py` (fake cursor). Result written to `docs/experiments/ops-supabase-engine.md`.
- **T-130**: identity gets `POSTGRES_HOST`, `IDENTITY_DB_USER`, `IDENTITY_DB_PASSWORD` in Compose and `.env.example`. `bootstrap-roles` gets `BOOTSTRAP_PASSWORD_IDENTITY` and `BOOTSTRAP_PASSWORD_SOCIO`. Bootstrap exits 2 outside the local profile on empty or `CHANGE_ME*` passwords. `ops/check_configuration.py` checks the wiring.
- **T-131**: Redis ACL users `edge` (gateway, `invent:rate:*` only) and `data` (streams, locks) rendered by `deploy/redis/entrypoint.sh` from `deploy/redis/acl.rules`. Default user is off. `REDIS_URL` carries the user. Production settings require a username. Configuration check fails on default-user usage.

## Commands run
- `python3.12 -m venv /tmp/venv && pip install '.[dev,streamlit,identity,sqltest]'`: ok.
- Per task: task acceptance pytest, `make static`, `make test`: all passed. Final: 1181 passed, 2 skipped, 5 xfailed. `python ops/check_configuration.py`: OK (98 variables).
- ACL rules exercised against a local Redis 7.0 (rate limiter, streams, `check_redis`).
- `tests/unit/test_sql_consistency.py` ran inside `make test`. No SQL was added.

## Assumptions
- `socio_app` needs only a role and password; no service uses it yet.
- `IDENTITY_DB_PASSWORD` derives from `BOOTSTRAP_PASSWORD_IDENTITY` in Compose.
- Local profile means `APP_PROFILE=local` and a dev-like `ENVIRONMENT`.

## Unresolved risks
- No Docker: the Compose stack, the redis entrypoint override, and identity startup ordering (identity now waits on migrate and bootstrap-roles; gateway waits on identity) are untested. The CI e2e stage is the first real run.
- T-132 grant is unverified on a real non-superuser role; `execute_tolerant` swallows all `psycopg.Error` on extension creation; the old `execute()` is now unused.
- `ops/bootstrap_env.py` `SECRET_KEYS` (outside scope) does not generate the two new bootstrap passwords or the Redis passwords, so `.env` keeps local defaults. Follow-up task needed.
- Redis ACL: dynamically built keys are not caught by tests; new commands or prefixes must be added to `acl.rules`. `redis-cli` now needs `--user data --pass`.
- A stray ruff reorder of `tests/unit/test_score_event.py` was reverted as out of scope.
