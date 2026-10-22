# Run r1-j-docs

Branch: agent/r1-j-docs. Tasks: T-166 (done), T-167 (done). Both done by the coordinator directly (small, documentation and test only; no subagent needed).

## T-166 Event chain rollout runbook
- New `docs/runbooks/event-chain-rollout.md`: what the flag changes, consumers first then producers, checks before and after, rollback, and the known `trace_chain` gaps. Every claim cites a repository file; a test checks each cited path exists.
- `docs/runbooks/go-live.md`: one link line. `tests/unit/test_runbook.py`: three new tests (ASCII and known variables, required content and real file paths, link from go-live).

## T-167 SPEC tasks lines
- 18 `tasks:` lines in SPEC.md rebuilt from the `spec:` lines of TASKS.md (only additions of ids; nothing else in SPEC.md changed). Every requirement that was cited already had a line, so no `none, reason` line was touched.
- `tests/unit/test_spec_tasks_lines.py`: two new tests, one that fails when a citing task is missing from a line, one that proves the detector on a synthetic omission.

## Commands
- `python3.12 -m venv /tmp/venv`, `pip install '.[dev,streamlit,identity,sqltest]'`: OK.
- `pytest -q tests/unit/test_runbook.py` 9 passed; `python ops/check_language.py` OK.
- `python ops/check_traceability.py` OK (119 requirements, 147 tasks); `pytest -q tests/unit/test_spec_tasks_lines.py tests/unit/test_traceability.py` 13 passed.
- `make static` and `make test` before each commit: pass (1558 passed, 2 skipped, 4 xfailed before T-167 tests).

## Assumptions
- The runbook describes the `--chain` mode of `ops/e2e_check.py` as pending T-164 (not merged on this branch) and gives a manual SQL check meanwhile.
- Flag is passed to services through `.env` / `env_file`; T-164 adds the explicit Compose passthrough.

## Unresolved risks
- Rebuilt lines for REQ-QA-001, REQ-SEC-005 and others now list many more tasks than before, by the rule of the task; reviewers may find that noisy.
- The runbook's recreate command (`docker compose up -d core ml`) uses service names assumed from the stack; not run (no Docker).
- Chain SQL has still never run on real Postgres (see RUN-r1-i-events).
