# Invent Market Tracker - Tasks

Status: DRAFT TEMPLATE. Regenerate after the ADR consolidation. Task IDs are never reused.

Each task cites the requirements in `SPEC.md` that it satisfies. The run protocol (branch, commit, push, evidence, blocked handling) is defined in `AGENTS.md`.

## Task format

```text
### T-NNN Short title
- status: todo | in_progress | done | blocked
- spec: REQ-AREA-NNN, REQ-AREA-NNN
- owner: role id from .codex/agent-registry.yaml
- depends: T-NNN or none
- write_scope: paths the task may change
- context: files to read before starting (and nothing more)
- size: S | M | L
- acceptance: commands that must pass
- evidence: filled by the agent when done
```

Size is a rough token cost guide: S under 1 USD, M 1 to 3 USD, L 3 to 8 USD.

## Run plan

| Run | Purpose | Tasks | Suggested session budget |
| --- | --- | --- | --- |
| smoke | Prove clone, branch, commit, and push work from the sandbox | T-000 only | 1 USD |
| f0-a | Test foundation and checks | T-001 to T-003 | 6 USD |
| f0-b | Service tests and CI | T-004 to T-007 | 10 USD |

Adjust budgets after the smoke run shows real cost per task. Blocked tasks do not consume budget.

## Tasks

### T-000 Smoke test
- status: todo
- spec: REQ-QA-001
- owner: coordinator
- depends: none
- write_scope: docs/runs/
- context: AGENTS.md
- size: S
- acceptance: `make static`
- note: create `docs/runs/RUN-smoke.md` with one line per check performed (git branch, commit, push, `make static` result). If `make static` cannot run because tools are missing, record exactly which ones.
- evidence:

### T-001 Add pytest and a make test target
- status: todo
- spec: REQ-QA-001, REQ-QA-002
- owner: coordinator
- depends: T-000
- write_scope: pyproject.toml, Makefile
- context: pyproject.toml, Makefile
- size: S
- acceptance: `make test` runs and exits 0 with the placeholder suite; `make static` still passes
- note: add pytest and pytest-asyncio to the `dev` extra, add a `test` target, and add a trivial `tests/unit/test_smoke.py`. Keep versions bounded like the existing dependencies.
- evidence:

### T-002 Add the spec to task traceability checker
- status: todo
- spec: REQ-TRC-001
- owner: qa-reviewer
- depends: T-001
- write_scope: ops/check_traceability.py
- context: SPEC.md, TASKS.md, ops/check_language.py
- size: M
- acceptance: `python ops/check_traceability.py` exits 0 on this repository and exits non-zero when a task cites an unknown requirement ID
- note: the script needs a module docstring in the repository style. Parse `### REQ-` headings and `spec:` lines. Wire it into `make static` through a follow-up devops task or ask the coordinator.
- evidence:

### T-003 Unit tests for shared helpers
- status: todo
- spec: REQ-QA-002, REQ-SEC-001, REQ-DAT-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_common.py
- context: libs/common/pagination.py, libs/common/retry.py, libs/common/time.py, libs/common/log_redaction.py
- size: M
- acceptance: `pytest -q tests/unit/test_common.py`
- note: cover cursor round trip and ordering, retry attempts and bounded backoff, UTC millisecond helpers, and redaction of document numbers, emails, and secret-bearing keys. Report production defects instead of changing production code.
- evidence:

### T-004 Gateway tests
- status: todo
- spec: REQ-QA-002, REQ-GW-001
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_gateway.py
- context: services/gateway/app/main.py, libs/common/settings.py, libs/common/security.py
- size: M
- acceptance: `pytest -q tests/unit/test_gateway.py`
- note: use an in-process ASGI client and fake upstreams. No Redis or Postgres. Cover `/health`, `/ready` with a failing upstream, 404 for non-public paths, and edge secret enforcement.
- evidence:

### T-005 Outbox relay and idempotent consumer tests
- status: todo
- spec: REQ-QA-002, REQ-EVT-001, REQ-EVT-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_outbox.py
- context: services/core/app/outbox_relay.py, services/core/app/worker.py, libs/common/redis_streams.py, libs/contracts/events.py
- size: L
- acceptance: `pytest -q tests/unit/test_outbox.py`
- note: use fakes for the database and Redis. Verify unpublished events are published once marked, retried on failure, and that a repeated event ID is applied once.
- evidence:

### T-006 Ingest webhook signature tests
- status: todo
- spec: REQ-QA-002, REQ-SEC-002
- owner: qa-reviewer
- depends: T-001
- write_scope: tests/unit/test_ingest_webhook.py
- context: services/ingest/app/main.py, libs/common/security.py, libs/common/settings.py
- size: M
- acceptance: `pytest -q tests/unit/test_ingest_webhook.py`
- note: cover a valid signature, a wrong signature, a stale timestamp, and a future timestamp outside the window.
- evidence:

### T-007 Add the unit job to CI
- status: todo
- spec: REQ-QA-001, REQ-CI-001
- owner: devops-engineer
- depends: T-003, T-004, T-005, T-006
- write_scope: .github/workflows/ci.yml
- context: .github/workflows/ci.yml, Makefile
- size: S
- acceptance: the workflow file parses as YAML and contains a `unit` job with `needs: static` that runs `make test`
- evidence:

### T-008 Reconcile schema and role names
- status: todo
- spec: REQ-DAT-001
- owner: database-engineer
- depends: T-001
- write_scope: migrations/, ops/bootstrap_roles.sql.example
- context: migrations/README.md, migrations/20261017_0001_expand_initial_schemas.sql, migrations/20261017_0002_arch_v22_foundations.sql, ops/check_migrations.py
- size: M
- acceptance: `python ops/check_migrations.py`
- note: D-01 is closed in `SPEC.md` (7 schemas, 14 roles, `socio` canonical, see SPEC section 3.2). Follow expand/contract: add new names in an expand migration; remove `socioeconomic` and never create `analytics` through a `contract_` migration in a separate commit.
- evidence:
