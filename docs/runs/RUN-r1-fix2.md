# Run report r1-fix2 (FIX-CI)

Branch: `agent/r1-fix2`. Environment: Python 3.12 venv, `pip install '.[dev]'` succeeded (PyPI reachable).

## Result
`make static` and `make test` both pass (252 passed, 1 skipped, 4 xfailed).

## Changes
1. mypy: `rate_limiter.py` passes the window and limit to `redis.eval` as `str`; `redis_client.py` casts the `Redis.from_url` result; `ingest` and `core` repositories annotate `row_id` and `existing_signal_id`.
2. Generated `tests/unit/snapshots/model_schemas.json` with `UPDATE_SNAPSHOTS=1`.
3. `tests/unit/test_outbox.py` (7 failures): **the code was wrong, not the test.** `read_consumer_batch` in `libs/common/redis_streams.py` treated the pending read (`XREADGROUP ... 0`) as non-empty whenever it returned `[(stream, [])]`. Redis returns that shape when nothing is pending, so the call never reached the `>` read and consumers never saw new messages, and so never recorded the processed event. This is a real production bug, not only a test one. The fix checks whether any stream has entries.

## Risks
`redis_streams.py` is shared by the ingest, analytics and ml workers, so all three change behavior (for the better). The snapshot is a fresh baseline, so it was not compared with any earlier contract.
