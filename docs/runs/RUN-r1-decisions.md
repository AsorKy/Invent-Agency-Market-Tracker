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
5. ML has no outbox table. D-21 needs `ml.outbox_event` and a relay instance for it; D-17 says one relay. T-176 reuses the relay code per owning schema and stops with `blocked` if that conflicts with D-17. This is an implication the owner should confirm.
6. D-33: the identity service issues service tokens, but issuing needs an existing token, so n8n could need the shared key. T-185 stops and reports instead of putting the shared key in n8n.
7. D-34: the target date was left as a placeholder in the owner text; it stays open in D-34 and in go-live section 17.
8. D-24 gives a core route only; a strategist reaches it through the gateway, so T-180 (gateway route) was added by the coordinator.
9. Deferred, not for any wave until the owner schedules them: T-182 (R2), T-183 (L2).
10. T-187 is a record for the owner; agents do not run it. Its file is a PENDING skeleton.

## Unresolved risks

- T-082 enables RLS on three tables written by several code paths; every writer must set the tenant context. The task requires a real Postgres test for each.
- T-176 adds a second outbox; if the relay cannot be shared the decision D-17 needs the owner.
- Wave r1-l is running and edits TASKS.md and SPEC.md lines of T-170 to T-173; merging this pull request may need a small conflict resolution in TASKS.md.
