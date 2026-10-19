# Run r1-d-e2e

Branch: `agent/r1-d-e2e`. Tasks: T-072 done, T-077 done, T-065 done. None blocked.

## Commands
- Setup: python3.12 venv, `pip install '.[dev,streamlit]'`. The identity extra (Django, cryptography) is not in that command, so `pip install '.[identity]'` was also needed. Without it, `make test` fails at collection and mypy fails on `services/identity/iam/keys.py`.
- `make static`: clean. `make test`: 778 passed, 2 skipped, 8 xfailed.
- `make coverage`: 86.6% for libs+services, 99% for libs/domain.
- No SQL was added, so `test_sql_consistency` was not needed.

## Changed files
ops/seed/{__init__,__main__,loader}.py, ops/seed/README.md, tests/unit/test_seed.py, tests/unit/support_pipeline.py, tests/unit/test_pipeline_flow.py, pyproject.toml (coverage settings), Makefile (`coverage` target), TASKS.md (own blocks).

## Assumptions and risks (T-072)
- `clear` matches demo companies by deterministic slug and removes them through core opt-out, because core has no delete-by-source API. Downstream rows (scores, rollups, briefs) are not removed.
- The seed does not write `ingest.raw_snapshot` or `ingest.entity_candidate` (they need a DB and Redis).
- Leads are not seeded; they depend on auto-qualification.
- `make seed` and `make seed-clear` targets belong to T-074.

## Production defects found by T-077 (strict xfail tests pin each)
1. D-1 (high): ingest `SnapshotFlow._deliver` (services/ingest/app/to_core.py) sends no `tenant_id` on company upsert. Events get a NULL tenant and the relay dead-letters them, so ingested data never reaches Pulse. The main-flow tests use a shim for this.
2. D-2: `GET /api/v1/leads/{id}/brief` always returns 404, because `lead_digest` in services/brief/app/main.py passes `lead_id` as `company_id`.
3. D-3: core has no `brief-facts` route, so `HttpCoreSource` cannot read facts (also in RUN-r1-c-brief.md). The test uses a fake source.
4. D-4 (severe): ML publishes `lead.score_updated` with no `payload` or `tenant_id`. Pulse raises KeyError and its worker sticks on that entry. This contradicts REQ-EVT-005.

Coverage gaps: several Pulse endpoints return empty projections, `/pulse/segments` returns 501, and the ingest processor and ml consumer loops are not run by the pipeline test.

## Modules below 80% (T-065)
core/app/repository.py 45%, ingest/app/processor.py 0%, core/app/worker.py 38%, brief/app/core_source.py 41%, ml/app/repository.py 35%, ml/app/main.py 72%, ml/app/segmentation.py 74%, pulse/app/rollup_close.py 69%, core/app/dead_letter.py 76%, ingest/app/repository.py 73%, ingest/app/main.py 76%, core/app/republish.py 78%, core/app/main.py 79%, streamlit/Home.py 0%, identity wsgi.py and migration 0%, libs/common db.py 56%, rate_limiter.py 65%, redis_client.py 57%. Reason: SQL, `main()` loops, or entry scripts that need live Postgres, Redis or Streamlit. The integration tier covers the SQL.
