# Run report r1-d2-qa

Branch: agent/r1-d2-qa. Tasks: T-126 done, T-127 done. No blocked tasks.

## T-126
New tests/unit/test_cov_infra.py (46 tests) and tests/unit/support_cov.py. Coverage from that file alone: libs/common db.py, rate_limiter.py and redis_client.py 100%; core repository.py and brief core_source.py 100%; core worker.py 98% (only the `__main__` guard is missed).

## T-127
New tests/unit/test_cov_services.py (42 tests) and tests/unit/support_cov_services.py. Coverage from that file alone: ingest processor 98%, ingest repository 99%, ingest main 100%, ml repository 100%, ml main 100%, ml segmentation 98%, pulse rollup_close 97%, core dead_letter 99%, core republish 96%.

## Checks
make static passes. make test: 978 passed, 2 skipped, 8 xfailed. make coverage passes (libs/domain 99%).

## Notes
- The tests use fake engines and connections plus fakeredis. The Lua `eval` is stubbed, and the ml extra is faked.
- No production defects found. No SQL was added, so SQL consistency was not affected.
- The `__main__` guards and a few branch lines remain uncovered by design.
