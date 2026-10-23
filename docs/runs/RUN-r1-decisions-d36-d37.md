# Run report: owner decisions D-36 and D-37

Date: 2026-10-23. No code changed; SPEC.md, TASKS.md and this report only.

## Decisions

| Decision | Option | Tasks it unblocks |
| --- | --- | --- |
| D-36 retention and opt-out under RLS | A: tenant-aware compliance routes, tenants listed from identity | T-191 (rewritten), T-194 (new) |
| D-37 scope of the core.signal unique key | B: `(tenant_id, source, source_external_id)`, expand then contract | T-195 (expand), T-196 (contract) |

## Differences between the decision text and the repository

- The decision text named T-191 and T-192. T-191 already existed (blocked, retention) and is rewritten as the decision asks. T-192 (doctor `--tenants`) and T-193 (verify-live role) were merged in PR #80, so the signal key work took the next free ids: T-195 (expand), T-196 (contract). Identity needed a tenant list, which did not exist: T-194.
- The gateway has no opt-out or purge route on main, so there is nothing to forward `X-Tenant-Id` through. The callers are the seed and internal callers. T-191 requires the header at the core route and does not add a public gateway route; that would be a separate API decision.
- Migration numbers are pinned: 0014 is T-082 (merged), 0015 is T-181 (wave r1-n, open), 0016 is the ml outbox (merged), so T-195 uses 0017 and T-196 uses `contract_20261024_0018`.

## Clashes with open waves

- T-191 and T-195 both edit `services/core/app/repository.py`, as do T-181 and T-188 in the open session r1-n-validation. Launch them after that session merges.
- T-195 and T-196 must not share a wave (INV-06).
- Priority: T-195 before a second tenant is onboarded; T-191 is a go-live prerequisite.
