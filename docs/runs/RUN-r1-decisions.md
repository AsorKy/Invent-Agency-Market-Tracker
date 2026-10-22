# RUN r1-decisions: owner decisions of 2026-10-22

The owner closed the 14 open points. This run applied them to SPEC.md, TASKS.md and the runbooks. No application code was changed. Decisions are D-20 to D-35 in SPEC.md section 5; D-06 and D-13 were updated.

## Decisions applied

| Point | Decision | Where | Tasks |
| --- | --- | --- | --- |
| 1 Chain payloads | Typed, versioned, `parent_event_id` mandatory | D-20, REQ-EVT-008 | T-174 |
| 2 Parent ids | Contract carries the parent id; `signal.scored` through an ml outbox | D-21, REQ-LNS-012, runbook event-chain-rollout section 6 | T-175, T-176 |
| 3 Stage events | Stage change equals pipeline plus forecast updated; Pulse consumes; no forecast figures | D-22, D-35, REQ-PUL-004 | T-177 |
| 4 Score features | `demo-1` frozen as permanent baseline; production definition is L3 in shadow | D-23, REQ-LNS-007 | T-178 |
| 5 Validation inputs | Strategist writes inputs by hand; meta-ML metrics through events in R2 | D-24, D-25, REQ-LNS-014, REQ-LNS-016 | T-179, T-180, T-182 |
| 6 Funnel per project | Per project through `core.lead.project_id` | D-26, REQ-LNS-015 | T-181 |
| 7 Funnel versioning and 403/404 | Only KILL versions; 403/404/not_in_layer is now a SPEC contract | D-27, REQ-LNS-009, REQ-API-002 | none (T-163 stays) |
| 8 Token keys | Shared key accepted now, per-service keys in L2 | D-28, REQ-SEC-005 | T-183, T-184 |
| 9 RLS exemptions | Three tables get RLS, five stay exempt with reasons | D-29, REQ-DAT-005 | T-082 |
| 10 Baseline | No new baseline | D-30 | none |
| 11 PostgreSQL | 16 in run 1; 17 by experiment before F4 | D-31 | none |
| 12 Bright Data | Owner captures and confirms the dataset id | D-13 | none |
| 13 Docker run | Owner or qa-reviewer, recorded in RUN-golive-docker.md | D-32 | T-187 |
| 14 Go-live | Minted token for the socio refresh, owners fixed, date open, prerequisites listed | D-33, D-34, go-live runbook sections 12 and 17 | T-185, T-186 |

## Files changed

SPEC.md (requirements, decisions, change log), TASKS.md (T-082, T-174 to T-187), docs/runbooks/event-chain-rollout.md (section 6), docs/runbooks/go-live.md (sections 12 and 17), docs/runs/RUN-golive-docker.md (new, marked PENDING), this report.

## Verification against the repository

- The shared token facts hold: `v1.<service>.<expires-at>.<hex-hmac>`, 60 second default in `libs/common/internal_auth.py`.
- `seed` and `verify_live` are accepted in any environment today (allow lists in `services/core/app/main.py`), so the SPEC statement "only outside production" needs code: T-184.
- The RLS registry has eight tables as the owner wrote; `analytics.processed_event` is not in it and no live test or migration outside the historical expand files and the contract migration names it.
- Migration chain: `contract_20261018_0003` and `contract_20261020_0008` exist and retire the legacy schemas. The remaining mentions of `analytics.` and `socioeconomic.` are the historical expand files, tests that assert the schemas are gone, and the fake `analytics.fact_signal` branch in `tests/unit/support_outbox.py` (dead code, harmless).
- F-r1e-02 (identity database wiring) is already in `docker-compose.yml`, `.env.example` and `ops/check_configuration.py`; it is now listed as a checkable prerequisite. The `verify-live` port caveat was already in section 7 of the go-live runbook; it is now a prerequisite closed by T-186.

## Points where the text and the repository differ, or needed a reading

1. Chain payload of `signal.tagged`: the owner text was garbled (`tags:{sourype}`); read as `tags{source, signal_type}`, the shape T-159 produces. The list gave `parent_event_id` to every event but not in the `signal.tagged` line; the rule "all carry it" was applied.
2. `rues_matched`, `project_id|null` and other garbled tokens were read as written in the canonical list.
3. Pulse: the owner text names `pulse.project_snapshot (stage, exited_at)`; the real column `exited_at` is in `pulse.project_stage_visit`. T-177 uses the real tables.
4. The Computrabajo dataset id guess (`computrabajo-tech-co`) is not in the repository; D-13 does not repeat it.
5. ML has no outbox table. D-21 needs `ml.outbox_event` and a relay instance for it. RESOLVED by the owner (A1): one relay implementation, one instance per outbox schema; D-17 was clarified and T-176 is unblocked (shared module `libs/messaging/relay.py`).
6. D-33: the identity service issues service tokens, but issuing needs an existing token. RESOLVED by the owner (B1): a dedicated single-purpose mint credential, never the shared key; fallback B2 (rotated static header, D-33 reopened) only if it cannot be bounded; B3 is forbidden.
7. D-34: the target date was left as a placeholder in the owner text; it stays open in D-34 and in go-live section 17.
8. D-24 gives a core route only; a strategist reaches it through the gateway, so T-180 (gateway route) was added by the coordinator.
9. Deferred, not for any wave until the owner schedules them: T-182 (R2), T-183 (L2).
10. T-187 is a record for the owner; agents do not run it. Its file is a PENDING skeleton.

## Unresolved risks

- T-082 enables RLS on three tables written by several code paths; every writer must set the tenant context. The task requires a real Postgres test for each.
- T-176 moves the generic relay to `libs/messaging/relay.py`; core behavior and public names must not change (its tests are in the acceptance list).
- Wave r1-l is running and edits TASKS.md and SPEC.md lines of T-170 to T-173; merging this pull request may need a small conflict resolution in TASKS.md.

## Second round: owner decisions A, B and C (same pull request)

- A1 (D-17, D-21, D-29, T-176): D-17 text replaced (one implementation, one instance per outbox schema); D-21 points to it; D-29 adds the ml outbox tables as exempt infrastructure; T-176 rewritten and unblocked, with the RLS_EXEMPT entries for the ml outbox tables in its scope.
- B1 (D-33, T-185, go-live section 12): dedicated mint credential with scope pulse only; `INTERNAL_SERVICE_TOKEN` never enters n8n; fallback B2 documented; B3 forbidden. The workflow stays inactive. The outcome (B1 or B2) is decided by the T-185 agent and will be reported there.
- C1 (T-186, go-live section 17, D-32): verify-live runs inside the Compose network; no core or ingest port is published; the loopback publishing draft is withdrawn. The caveat paragraph of section 7 stays until the in-network run is proven.
- Cleanup: the dead `analytics.fact_signal` branch and its `fact_signals` attribute were removed from `tests/unit/support_outbox.py` in this pull request (1606 unit tests pass), so T-082 does not need that file.
- Numbering: pull request #70 (wave r1-l tasks T-170 to T-173) is already merged and no r1-l branch exists, so there is no rebase to do; T-082 and T-174 to T-187 and D-20 to D-35 do not collide with any existing id.
- Target date of go-live: the owner message again carried the placeholder `[PON TU FECHA]`, not a date. D-34 and go-live section 17 keep "not set"; no date was invented.
