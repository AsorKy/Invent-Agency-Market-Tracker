# Run r1-q-gateway

## Tasks
- T-197 done: `POST /api/v1/compliance/opt-out` (admin only) forwards to `POST /internal/core/compliance/opt-out`. The gateway sets `X-Tenant-Id` from the JWT and drops client-supplied values. Purge stays internal (`compliance/purge` answers 404). The route is documented in the registry with `OptOutRequest` as the request model.
- Other files: removed the opt-out line from `NOT_PUBLIC` in `tests/unit/test_openapi.py` (the route is now public); one line each in `docs/compliance/processing-activities.md` and `docs/runbooks/go-live.md`; `docs/api/openapi.json` regenerated (134 insertions, 0 deletions, so additive); snapshots unchanged. No migration.

## Commands
- venv python3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: ok.
- `make openapi`, `UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py`: 5 passed.
- `make static`: passed. `make test`: 1742 passed, 2 skipped, 4 xfailed.

## Assumptions
- A body that names another tenant is not read by the gateway. Core's `OptOutRequest` has `extra=forbid`, so core answers 422. The tenant sent upstream is always the JWT tenant (tested, including a forged `X-Tenant-Id` header).
- No SQL or RLS change, so no new integration case.

## Risks
- The route's 200 response is a generic JSON object (core returns a free dict).
- The CI e2e stage was not run (no Docker).
