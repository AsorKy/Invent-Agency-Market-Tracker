# Run report r1-b-b (lane B, part 1)

Branch: `agent/r1-b-b`. Setup: python3.12 venv at /tmp/venv, `pip install '.[dev]'` succeeded.

## Tasks
Done: T-040, T-041, T-042, T-043, T-044, T-047 (one commit each, plus one extra commit declaring `pyyaml` in `pyproject.toml`).
Blocked: none.
Left `todo` on purpose (depend on lane A): T-045 (needs T-020), T-046, T-048, T-049.

## Commands
Before every commit: `make static` (ruff, mypy, guards) and `make test` passed. Final: 342 passed, 1 skipped, 4 xfailed. `python ops/check_workflows.py`: OK (3 workflows). Task acceptance tests all passed (see `evidence:` lines in TASKS.md).

## Assumptions and decisions
- Entity resolution scorer: pure-Python `difflib` token-sort ratio behind `score_names` (rapidfuzz/Splink not installed; swappable). Boundary is "above 0.9" for auto match.
- `snapshot_id` column is treated as `external_id`; existing UNIQUE(source, snapshot_id, payload_sha256) satisfies REQ-ING-001.
- Mappings for maps and computrabajo are `unverified: true`: `tests/fixtures/brightdata/` does not exist (D-13). Computrabajo field names and dataset id `computrabajo-tech-co` are guesses.
- Budget reserve fails closed (no budget row = blocked). INGEST_MODE is global, not per source.
- Added `pyyaml` to pyproject dependencies (it was only transitive).
- Added `GET /internal/ingest/budget/{source}` and `POST /internal/ingest/trigger/{source}` in ingest (budget endpoint required by the n8n workflows).

## Changed files
services/ingest/app/{snapshot,budget,mapping,connectors,source_config,poll,main,repository}.py; services/mocks/app/main.py; libs/domain/entity_resolution.py; libs/domain/source_mappings/{maps.yaml,computrabajo.yaml,README.md}; orchestration/n8n/{wf_maps_medellin.json,wf_computrabajo_tech.json,README.md}; pyproject.toml; TASKS.md; tests/unit/test_{ingest_snapshot,ingest_budget,connectors,mocks,ingest_poll,source_config,entity_resolution,workflows}.py.

## Unresolved risks
- SQL paths (process_snapshot previous lookup, BudgetRepository, pending_snapshot, lifespan poll task) are only tested with fakes, not real Postgres.
- `ingest.failed_fetch` has no unique key on (source, external_id); concurrent first failures could duplicate (needs a migration, coordinator decision).
- n8n workflows trigger Bright Data directly, so those triggers are not tracked by the poller; consider routing through `POST /internal/ingest/trigger/{source}`.
- Poller vs webhook race could yield two raw_snapshot rows if payload shapes differ.
- No SQL `ResolutionStore` yet (sync port vs async repository); T-045 must add an adapter and map decisions to core calls. `signal_type` values in mappings not validated against core taxonomy.
- Redis enqueue mark expires after TTL; redelivery then re-enqueues (consumer idempotent).
