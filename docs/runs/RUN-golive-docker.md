# RUN go-live Docker run

Status: PENDING. Nobody has run the steps below yet. Decision D-32 (owner, 2026-10-22): the owner, or qa-reviewer, runs them on a machine with Docker before go-live and fills this file in. Task T-187 tracks it. Agents do not run these commands.

Remove the word PENDING from the status line only when every row below has a result.

| Command | Result | Date | Notes |
| --- | --- | --- | --- |
| `make test-integration` | not run | | |
| `make invent-tracker` | not run | | |
| `make doctor` | not run | | |
| `python ops/e2e_check.py` | not run | | |
| `python ops/e2e_check.py --chain` (after the CI step of T-164 is applied) | not run | | |

## Items the earlier reports marked unverified for lack of Docker

List each item with its result. Sources: `docs/runs/RUN-r1-e.md` (finding F-r1e-10) and the "unresolved risks" of the later run reports.

## Unresolved risks after the run

Write what is still open.
