# Seed

## Raw synthetic files

```bash
python -m datagen build --city all --orgs 200 --days 180 --seed 42 --as-snapshots
```

Writes JSON files only; nothing is loaded.

## Demo data (REQ-RUN-003)

```bash
python -m ops.seed load    # make seed
python -m ops.seed clear   # make seed-clear
```

Options for both: `--orgs` (default 60), `--days`, `--seed`, `--as-of`.
`clear` must use the same values as `load`. The Make targets belong to T-074.

How it works:

- `load` builds the datagen dataset, turns it into `maps` and `computrabajo`
  snapshot records, and runs each record through the ingest `SnapshotFlow`
  (mapping, mock RUES resolution, delivery) against core's internal API
  (`CORE_URL`, `INTERNAL_SERVICE_TOKEN`). Core's outbox, relay, and consumers
  then fill scores, rollups, and briefs.
- Rows are tagged `source=demo`: companies carry `source_ids={"demo": "demo"}`
  and signals use `source="demo"` with the original source in
  `payload.origin_source`.
- It is idempotent: core upserts companies by deterministic slug and ignores
  duplicate signals.
- `clear` never writes into another service's schema. It recomputes the
  deterministic demo company slugs, lists companies through core's API, and
  removes the matches with core's opt-out operation (signals go with them).
  Derived rows in other services are removed by the cascade of core events, not
  by the seed.

Limits: leads are not seeded directly; they come from auto-qualification.
Tests run offline with an in-memory core (`tests/unit/test_seed.py`).
