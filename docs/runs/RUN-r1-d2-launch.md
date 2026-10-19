# Run report r1-d2-launch

Branch: `agent/r1-d2-launch`

## Outcome

No task was implemented. T-074, T-075, T-076, T-078, T-079 and T-064 are blocked on unmet dependencies.

## Reason

The run protocol only starts a task whose `depends` entries are all `done`. On `main` they are not:

| Task | Unmet dependency on main | Where the dependency exists |
| --- | --- | --- |
| T-074 | T-073, T-072, T-070 (todo) | done only on unmerged `agent/r1-d-ops` (T-073), `agent/r1-d-e2e` (T-072), `agent/r1-d-hardening` (T-070) |
| T-075 | T-074 | blocked in this run |
| T-076 | T-075 | blocked in this run |
| T-078 | T-071, T-077 (todo) | done only on unmerged `agent/r1-d-ops` (T-071), `agent/r1-d-e2e` (T-077) |
| T-079 | T-076 | blocked in this run |
| T-064 | T-063 (blocked), T-070 (todo) | T-063's workflow patch is saved in `agent/r1-d-ops`; T-070 only on `agent/r1-d-hardening` |

T-074 would need `ops/seed`, `ops/export_openapi.py` and the compose file for its targets. I did not merge or cherry-pick other sessions' unreviewed branches into this one, because that would mix their work into this PR and risk conflicts in `Makefile` and `TASKS.md`.

## Commands executed

- `python3.12 -m venv /tmp/venv`, `pip install '.[dev,streamlit,identity]'`: succeeded (PyPI reachable).
- Inspected `TASKS.md` statuses on `main` and on remote `agent/*` branches.
- `make static` and `make test` were not run: only `TASKS.md` and this report changed.

## Changed files

- `TASKS.md` (status and blocker lines for the six tasks above)
- `docs/runs/RUN-r1-d2-launch.md`

## Next step

Merge the PRs for `agent/r1-d-ops`, `agent/r1-d-e2e` and `agent/r1-d-hardening` into `main`, then rerun this task list. The `.github/workflows/ci.yml` change for T-064 may still be rejected for lack of workflow scope and would then go in as a patch under `docs/runs/`.
