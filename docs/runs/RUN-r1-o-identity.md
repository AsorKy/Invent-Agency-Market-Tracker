# Run report r1-o-identity

## Tasks
- T-194 done: `GET /internal/identity/tenants` (identity, Django).

## Changes
- `permissions.py`: `CoreServiceToken` accepts only a valid signed token whose service is `core`.
- `views.py`: `TenantListView` returns `{"tenant_ids": [...]}` for active tenants only, 401 otherwise, GET only.
- `urls.py`: route added. `tests/unit/test_openapi.py`: NOT_PUBLIC entry under `# T-194`.
- `tests/unit/test_identity_tenants.py`: core accepted, ids only, inactive excluded, 401 for gateway/seed/verify_live/missing/garbage token, POST gives 405.

## Commands
- venv python3.12, `pip install '.[dev,streamlit,identity,sqltest]'`: ok.
- `pytest -q tests/unit/test_identity_tenants.py tests/unit/test_openapi.py`: 18 passed.
- `make static`: passed. `make test`: 1728 passed, 2 skipped, 4 xfailed.

## Assumptions
- The 401 (not DRF's default 403) is returned by the view itself, as the mint route does.
- No SQL or response model of a public contract changed, so no snapshot or openapi regeneration.
- The Django identity routes are not scanned by the NOT_PUBLIC test; the entry is added as the task requires.

## Risks
- T-191 must call this route with a signed `core` token. The gateway routing table was not touched.
