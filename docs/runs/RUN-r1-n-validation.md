# Run r1-n-validation

Branch: agent/r1-n-validation.

## Tasks
- T-179 done: `PUT /internal/core/projects/{id}/validation-inputs` (gateway-only token, ETag/If-Match, audit row, tenant scoped). Empty reason of V1, V2, V4, V5 is now `needs_strategist_input`.
- T-180 done: gateway `PUT /api/v1/projects/{id}/validation-inputs` (strategist, admin). Added by the coordinator to make D-24 usable. The coordinator also added `if-match` to the headers the gateway proxy forwards (`services/gateway/app/main.py`, outside the task write_scope); without it core answers 428. This also fixes the same latent gap on `PUT /admin/tenant-config`.
- T-181 done: migration 0015 (`core.lead.project_id`, FK NOT VALID, partial index), conversion fills it in `repository.insert_project` (actions.py, the real conversion site, is outside the write_scope), per-project funnel filters by `project_id`, plus a tenant-wide `funnel_metric` row.
- T-188 blocked (partially done): funnel exposes the gate event id through an optional callback; the backfill forwards `parent_event_id` when core sends one. Not closed: lead create `scored_event_id` (core stores no signal.scored id, INV-01) and the `signal.tagged` id on core's features route (both need files outside the write_scope, plus a migration and a worker handler). Needs a follow-up task. Runbook section 6 lists the gaps.

## Commands
- `pip install '.[dev,streamlit,identity,sqltest]'` in a Python 3.12 venv: ok.
- Per task: acceptance pytest sets, `make static` ok, `make test` ok. Final: 1720 passed, 2 skipped, 4 xfailed.
- `make openapi` and snapshots regenerated for T-180, no further diff. `oasdiff` not installed.
- Real Postgres integration cases (T-179 x2, T-181 x1) were written but not run (no Docker). CI is the real check.

## Assumptions
- Validation input ranges (rates 0..1, money within 1e12, percent within 1000, cash_flows 1..240, horizon H1/H2/H3) were chosen by the agent: confirm.
- Inputs version is stored at `metadata->'validation'->'version'`; PUT replaces the four sections and keeps `funnel`.
- Another tenant's project answers 404 `project_not_found` through the gateway, not the literal `not_found`.
- 428 is not documented in OpenAPI (`openapi_doc.py` out of scope).

## Risks
- No core GET for the inputs or their ETag: first write uses `If-Match: "0"`.
- Per-project funnel is empty for old leads until conversion fills `project_id` (no backfill).
- The index build in 0015 is not CONCURRENTLY (short write lock). The integration case expects the text "row-level security", unverified.
- `trace_chain` still reports gaps at the open joins (T-188).
