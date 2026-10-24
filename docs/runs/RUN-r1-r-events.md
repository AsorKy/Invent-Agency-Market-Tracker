# Run r1-r-events

## Tasks
- T-198 done (coordinator did it directly; no subagent was needed for one M task on files that overlap).

## Design (confirmed against D-21 and INV-01, no ml/core cross-schema read)
- Core features route: new optional `parent_event_id` per company, from `core.outbox_event` (exempt, filtered by tenant, read after `set_tenant_context`). `core_features.py` parses it; `backfill.py` already forwarded it.
- ml `lead.score_updated` entry: optional top-level `scored_event_id` (id of the `signal.scored` outbox row), only when `event_chain_emit` is on. Payload unchanged.
- Worker stores it in new nullable `core.company.score_event_id` (migration 0019, expand only, re-runnable). A score without an id clears the column so the id always belongs to the stored score.
- `create_lead` reads the stored id inside its transaction (tenant context set) when the body has none; both `main.py` and `imports.py` go through it, so they are unchanged. Not in the request hash.

## Commands
- `pytest -q` on test_event_chain_producers, test_ml_backfill, test_score_features, test_openapi, test_contract_snapshot, test_sql_consistency: pass.
- `python ops/check_migrations.py`: OK. `make openapi` and `UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py`: no public change except `table_catalog.json` (new column). No new /internal route, so NOT_PUBLIC is unchanged.
- `make static`: green. `make test`: 1751 passed, 2 skipped, 4 xfailed.

## Assumptions and risks
- Integration cases (tests/integration/test_sql_r1_flows.py) could not run (no Docker); CI is the real check. They fail on a missing tenant context (bare query returns no row).
- The lead-create read has no savepoint: it needs migration 0019 applied before the new core code runs.
- Out of scope files touched: tests/unit/test_core_score_column.py, test_score_event.py, snapshots/table_catalog.json (additive).
- Gap (c) of T-188 untouched.
