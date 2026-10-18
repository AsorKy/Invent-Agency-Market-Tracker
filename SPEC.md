# Invent Market Tracker - Specification

Status: DRAFT TEMPLATE. Regenerate the requirement list after the ADR consolidation (ADR-IMT-0001, ADR-IMT-0002, ADR-IMT-0003) is complete.

This file turns the ADRs into requirements that agents can implement and verify. It is the middle layer of the source-of-truth chain defined in `AGENTS.md`: ADRs, then this file, then `TASKS.md`, then code.

## How to use this file

- Every requirement has a stable ID: `REQ-<AREA>-<NNN>`. IDs are never reused.
- Every requirement states its source (an ADR section), how to verify it, and its phase.
- Every task in `TASKS.md` cites at least one requirement ID. Every requirement with `phase: F0` and `status: accepted` must be cited by at least one task. `ops/check_traceability.py` (task T-002) enforces both rules.
- `status` values: `proposed` (not yet confirmed by the owner), `accepted`, `deprecated`.

## 1. Scope

In scope now: phase F0 foundations (repository, schemas and roles, gateway, core with outbox, event contracts, mocks, CI stages 1 to 3).

Out of scope now: frontend, real Bright Data and RUES connectors, supervised scoring, socioeconomic indices, multi-city expansion.

## 2. Architecture invariants

These come from ADR-IMT-0001 section 3 and apply to every change.

| ID | Invariant |
| --- | --- |
| INV-01 | One service, one schema. No service reads another service's schema. |
| INV-02 | n8n orchestrates only. No business logic in workflows. |
| INV-03 | Every relevant change in core writes its event to the outbox in the same transaction. |
| INV-04 | Consumers are idempotent through an inbox table keyed by event ID. |
| INV-05 | The gateway holds no database credentials and does not route `/internal/*` externally. |
| INV-06 | Migrations follow expand/contract. Destructive changes use a `contract_` migration in a separate commit. |
| INV-07 | Instants are stored as timestamptz in UTC. Money is never a float. |

## 3. Service catalog

Fill the final list after the schema and service-count decisions (D-01, D-02) are closed.

| Service | Schema | Responsibility | Owner role | Repository state |
| --- | --- | --- | --- | --- |
| gateway | none | Single public entry, auth, rate limit, routing | lead-backend-engineer | scaffolded |
| identity | identity | Users, roles, tenants, tokens | proposed: lead-backend-engineer | scaffolded |
| ingest | ingest | Webhooks, entity resolution, enrichment | second-backend-engineer | scaffolded |
| core | core | Companies, signals, leads, pipeline, outbox | lead-backend-engineer | scaffolded |
| analytics | analytics | Rollups and consumers (see D-02) | second-backend-engineer | scaffolded |
| pulse | pulse | Operational KPI endpoints | proposed: second-backend-engineer | scaffolded |
| brief | brief | Per-lead digest with single-flight cache | proposed: lead-backend-engineer | scaffolded |
| ml | ml | Segmentation and scoring | second-backend-engineer | scaffolded |
| streamlit | none | Deep reporting | proposed: second-backend-engineer | scaffolded |
| mocks | none | Development and test doubles | qa-reviewer | scaffolded |

## 4. Requirements

Template entry:

```text
### REQ-<AREA>-<NNN> Short title
- status: proposed
- phase: F0
- source: ADR-IMT-000X section N
- statement: one testable sentence.
- verify: command or test path that proves it.
- tasks: T-NNN
```

### REQ-QA-001 Static checks pass on every commit
- status: proposed
- phase: F0
- source: AGENTS.md quality gates, ADR-IMT-0001 section 17
- statement: `make static` passes on every commit pushed by an agent.
- verify: `make static`
- tasks: T-001, T-007

### REQ-QA-002 Unit test suite exists and runs offline
- status: proposed
- phase: F0
- source: ADR-IMT-0001 section 17 stage 2
- statement: A unit test suite under `tests/unit/` runs with no network, database, or Redis, and is exposed through `make test`.
- verify: `make test`
- tasks: T-001, T-003, T-004, T-005, T-006

### REQ-GW-001 Gateway health, readiness, and isolation
- status: proposed
- phase: F0
- source: ADR-IMT-0001 sections 6.4 and 14.1
- statement: The gateway serves `/health` and `/ready`, has no database URL in its settings, and returns 404 for paths outside the public API prefix.
- verify: `pytest -q tests/unit/test_gateway.py`
- tasks: T-004

### REQ-EVT-001 Transactional outbox with at-least-once relay
- status: proposed
- phase: F0
- source: ADR-IMT-0001 section 7
- statement: Core persists a signal and its outbox event atomically, and the relay publishes unpublished events to Redis Streams without losing any on restart.
- verify: `pytest -q tests/unit/test_outbox.py`
- tasks: T-005

### REQ-EVT-002 Idempotent consumers
- status: proposed
- phase: F0
- source: ADR-IMT-0001 section 7.4 and amendment E1.4
- statement: Processing the same event ID twice produces the effect once.
- verify: `pytest -q tests/unit/test_outbox.py`
- tasks: T-005

### REQ-SEC-001 Log redaction
- status: proposed
- phase: F0
- source: ADR-IMT-0001 amendment E1.8
- statement: Structured logs mask document numbers and emails and never contain cookies, authorization headers, or secrets.
- verify: `pytest -q tests/unit/test_common.py`
- tasks: T-003

### REQ-SEC-002 Signed Bright Data webhook with replay window
- status: proposed
- phase: F0
- source: ADR-IMT-0001 amendment E1.1
- statement: The ingest webhook rejects requests with an invalid signature or a timestamp outside the configured replay window.
- verify: `pytest -q tests/unit/test_ingest_webhook.py`
- tasks: T-006

### REQ-DAT-001 Schemas and roles match the ADR
- status: proposed
- phase: F0
- source: ADR-IMT-0001 section 5.1
- statement: Migrations create exactly the schemas and roles the consolidated ADR lists, with consistent names across migrations, settings, and compose.
- verify: `python ops/check_migrations.py`
- tasks: T-008
- note: blocked by decision D-01.

### REQ-DAT-002 Time and pagination conventions
- status: proposed
- phase: F0
- source: ADR-IMT-0001 amendment E1.3 and section 15
- statement: Shared helpers produce UTC millisecond instants and stable keyset cursors.
- verify: `pytest -q tests/unit/test_common.py`
- tasks: T-003

### REQ-CI-001 CI runs the unit stage
- status: proposed
- phase: F0
- source: ADR-IMT-0001 section 17
- statement: The CI workflow runs unit tests after static checks and blocks on failure.
- verify: `.github/workflows/ci.yml` contains a unit job that depends on the static job.
- tasks: T-007

### REQ-TRC-001 Spec to task traceability
- status: proposed
- phase: F0
- source: this file
- statement: Every task cites existing requirement IDs, and every accepted F0 requirement is cited by a task.
- verify: `python ops/check_traceability.py`
- tasks: T-002

## 5. Open decisions

An agent must not resolve these. A task that depends on one stays `blocked` until the owner records the decision here.

| ID | Question | Evidence in the repository | Decision |
| --- | --- | --- | --- |
| D-01 | Final schema list and names | Migration 0001 creates `socioeconomic`, migration 0002 creates `socio`, `identity`, `pulse`, `brief`. ADR header text and body disagree on schema and role counts. | open |
| D-02 | Is `analytics` kept, or replaced by `pulse` plus `streamlit` | Both `services/analytics` and `services/pulse` exist. | open |
| D-03 | Service count: 5 plus gateway, or 7 plus streamlit | `README.md` says five services. `docker-compose.yml` runs more. | open |
| D-04 | Ownership of brief, pulse, identity, streamlit | Not listed in `agent-registry.yaml`. | open |
| D-05 | Supabase tier and VPS provider for the MVP | ADR-IMT-0001 section 22. | open |

## 6. Change log

| Date | Change |
| --- | --- |
| 2026-10-18 | Initial template generated from the repository state and ADR-IMT-0001 v2.2.1. |
