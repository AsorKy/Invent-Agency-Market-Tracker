# RUN r1-d2-ops

- T-125 done: `bootstrap-roles` one-shot service after `migrate`; all 10 migrate-dependent services now also wait for it; psycopg[binary] added to deploy/Dockerfile.python; tests extended in tests/unit/test_compose.py.
- Acceptance: test_compose (41 passed), check_configuration, make static, make test pass. Not run against real Docker/Postgres.
- Decision: migrate stays on the postgres superuser. A dedicated migrator role would be circular, because roles are created after migrate.
- Note: bootstrap default privileges are FOR ROLE <schema>_owner, so tables created by postgres rely on explicit grants. Bootstrap must re-run after every migrate, and compose up does that.
- Follow-ups: ingest poller entrypoint (poll.py has no `__main__`), brief worker module, move psycopg[binary] into pyproject (outside write_scope).
- Blocked: none.
