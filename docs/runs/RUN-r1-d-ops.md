# Run r1-d-ops

## Tasks
- T-071 Migration runner: done (`ops/migrate.py`, 7 tests; `test_sql_consistency.py` passes).
- T-073 Compose, images, health probes: done (39 tests; `docker compose config` exit 0).
- T-063 Verified encrypted backups: blocked on the workflow file only. Code, README and tests are committed. The push of `.github/workflows/backups.yml` was rejected (PAT has no `workflow` scope). The change is saved as `docs/runs/T-063-backups-workflow.diff` (a `.patch` extension is gitignored); apply with `git apply`. `test_workflow_contract` skips until it is applied.

## Commands
- Setup: python3.12 venv, `pip install '.[dev,streamlit]'`, plus `cryptography`, `django`, `djangorestframework` (missing from that extra set; needed by mypy and identity tests; environment only).
- Before each commit: `make static` and `make test` pass (final: 824 passed, 3 skipped, 4 xfailed).

## Assumptions
- Migration versions are file names; `contract_` prefix is ignored for ordering, so the contract file runs between 0002 and 0004.
- Backup secrets: BACKUP_SOURCE_URL, BACKUP_EPHEMERAL_ADMIN_URL, BACKUP_EPHEMERAL_PASSWORD, BACKUP_AGE_RECIPIENT (CI only). identity/pulse/brief RPO/RTO (24 h / <15 min) are not in ADR 18.2 and are flagged in the README.
- Coordinator added BOOTSTRAP_PASSWORD_{INGEST,CORE,PULSE,BRIEF,ML} to `.env.example` (compose needs them; check_configuration requires it).

## Changed files
ops/migrate.py, tests/unit/test_migrate.py, ops/backups/*, tests/unit/test_backups.py, docs/runs/T-063-backups-workflow.diff, docker-compose.yml, deploy/Dockerfile.python, tests/unit/test_compose.py, .env.example, TASKS.md.

## Risks
- Compose does not create the `<schema>_app` roles; nothing runs `ops/bootstrap_roles.py`, so role logins fail until a bootstrap step exists (T-074 should wire it).
- No standalone ingest poller (runs in the ingest API lifespan) and no brief worker entrypoint exist; both omitted from compose.
- Migrate service uses a postgres superuser DSN.
- Backups workflow not yet in the branch (see above).
