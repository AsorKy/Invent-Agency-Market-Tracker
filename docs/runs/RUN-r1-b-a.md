# Run report r1-b-a (lane A, first half)

Branch: `agent/r1-b-a`. Tasks: T-020 to T-027.

## Tasks
Done: T-020, T-021, T-022, T-023, T-024, T-025, T-026, T-027. Blocked: none.
Tasks ran sequentially because all write to `services/core/` (overlapping scope). One commit per task, each pushed.

## Commands
- `python3.12 -m venv /tmp/venv && pip install '.[dev]'`: OK (PyPI reachable).
- Before every commit: `make static` OK and `make test` OK. Final: 355 passed, 1 skipped, 4 xfailed.

## Assumptions
- Models for T-020 to T-027 live in `services/core/app/` rather than `libs/contracts/*` because `test_contract_snapshot` auto-scans `libs/contracts` against the frozen snapshot. Promotion to contracts is a coordinator decision.
- Tenant comes from the `X-Tenant-ID` header (and `X-User-ID` for config updates); the gateway (T-030+) must supply them.
- SCD2 company history goes to `core.audit_log` (no company history table exists).
- `company.updated` with `change=created|updated` is used for company creation (no `company.created` event type).
- Lead status changes emit no outbox event (no EventType); only `audit_log`.
- Lost is reachable only from Hot onward (REQ-COR-002); this removed Discovered->Lost and Qualified->Lost from `libs/domain/pipeline.py`.
- CSV parsing uses stdlib csv behind `ChunkParser`; polars is declared but was not installed. Internal import endpoint takes a raw CSV body (`python-multipart` not declared).
- `tests/unit/test_company_upsert.py` had a formatting-only change by ruff format during T-021.

## Changed files
services/core/app/{companies,leads,actions,tenant_config,qualification,dead_letter,republish,imports,retention}.py (new); services/core/app/{main,repository,worker,outbox_relay}.py; libs/domain/pipeline.py; docs/compliance/processing-activities.md; tests/unit/test_{company_upsert,lead_state,outcomes,lead_actions,tenant_config,auto_qualification,dead_letter,republish,imports,retention}.py; TASKS.md; this report.

## Unresolved risks
- All SQL in `repository.py` is untested without Postgres (mypy/ruff and fakes only).
- T-025: the dead-letter relay is NOT wired in. `outbox_relay.main` and compose still run the old relay because `tests/unit/test_outbox.py` pins it. Follow-up: devops changes compose to `python -m services.core.app.dead_letter`, qa updates the two `main` tests. Consumers (pulse, brief, ml) must honor `republish:lock:<target>` and the `replay`/`target` fields.
- Missing schema support: no unique index on `core.project.lead_id`, none on `core.company.nit`; no opt-out suppression list; `import_job.upload_id` NOT NULL blocks blob row deletion (content emptied instead); no reaper for crashed import jobs. Migrations need database-engineer/coordinator.
- Purge deletes companies with open leads after 12 months without signals (literal spec reading).
- Outbox payloads retain company data after opt-out (90-day retention).
- T-007 CI patch (from earlier run) still needs the owner.
