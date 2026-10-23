# RUN r1-m-platform

Branch: agent/r1-m-platform. Tasks: T-186 done, T-178 done.

## T-186 verify-live in the Compose network (devops-engineer)
- New `verify-live` service in docker-compose.yml under profile `tools`, no ports, `restart: "no"`; reaches ingest/core by service name on container port 8000. Makefile target uses `docker compose --profile tools run --rm`. `.env.example` comment, go-live runbook section 7 gets the new command; tests added.
- The in-network run is NOT proven (no Docker). The section 7 caveat is kept (retitled for runs outside the network). T-187 must confirm on a Docker machine.

## T-178 versioned score feature baseline (second-backend-engineer)
- `BASELINE_SOURCE_VERSION = "demo-1"` (old `SCORE_SOURCE_VERSION` kept as alias because core and seed import it, outside write_scope); version rule documented; tests pin full seed rows and a sha256 fingerprint of the formula sources per version; note docs/experiments/scoring-feature-definition.md. Formulas and seed output unchanged.

## Commands
- `pytest -q tests/unit/test_compose.py tests/unit/test_verify_live.py tests/unit/test_runbook.py`: 78 passed
- `python ops/check_configuration.py`: OK
- `pytest -q tests/unit/test_score_features.py tests/unit/test_demo_pipeline.py tests/unit/test_ml_backfill.py`: 29 passed
- `make static`: ok; `make test`: 1625 passed, 2 skipped, 4 xfailed

## Assumptions
- VERIFY_LIVE_* set as literals in the service environment (no new .env variable).
- The fingerprint hashes source text; any edit (even comments) in the formula functions requires a new pin.

## Risks
- Bind-mount permissions, `.env` pass-through and the `--inputs` mount are untested without Docker.
- Fingerprint depends on ruff format stability.
- No migrations, no new routes, no workflow changes.
