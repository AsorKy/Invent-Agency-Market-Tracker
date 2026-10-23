# Run r1-o-schema

## Tasks
- T-195: **blocked** (no code or migration committed).

## Reason
The task's acceptance and its note contradict each other (and the note's order conflicts with D-37's intent):
- Acceptance: two tenants insert the same `(source, source_external_id)`; each gets its own row and `create_signal` does not raise.
- Note: the global `UNIQUE (source, source_external_id)` (migrations/20261017_0001, inline in `core.signal`) stays until T-196.

In PostgreSQL, `ON CONFLICT (tenant_id, source, source_external_id)` handles only conflicts on that arbiter index. The second tenant's row still violates the global unique and raises `UniqueViolation`. So the integration case would fail on real Postgres, and I cannot commit a failing case. The per-tenant key can only fix the bug once the global key is gone.

## Options for the owner (not chosen by me; D-37 is closed)
1. Merge the expand (new unique index, `ON CONFLICT` on the new key, tenant-filtered fallback SELECT) and drop the global unique in the same step. This breaks INV-06's "contract in its own PR" and needs an owner waiver.
2. Keep the split. T-195 then ships migration and code, and its test asserts only same-tenant idempotency and that the fallback SELECT filters by tenant. The two-tenant case moves to T-196, which is what makes it pass. The acceptance text of T-195 must be rewritten.
3. Note: with the global key in place, a conflict on the global key from another tenant needs `ON CONFLICT DO NOTHING` without an arbiter. That would silently drop the signal and hide the collision, so I did not choose it.

## Commands
- Environment: `python3.12 -m venv /tmp/venv` worked; `pip install '.[dev,streamlit,identity,sqltest]'` could reach PyPI but was still downloading when I stopped. `make static` and `make test` were not run because the change is docs only (TASKS.md block and this report).
- No Docker or Postgres is available, so no SQL was executed.

## Assumptions
- Analysis based on reading `core.signal` DDL and `create_signal` in services/core/app/repository.py.

## Unresolved risks
- Until the owner resolves the contradiction, `create_signal` still raises for a second tenant with a colliding `source_external_id` (priority: before a second tenant is onboarded).
