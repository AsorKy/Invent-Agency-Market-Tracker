# Run report r1-d2-core

Branch: `agent/r1-d2-core`

## Outcome

No task was executed. Both requested tasks are blocked: T-084 and T-085.

## Reason

`TASKS.md` has no task with these IDs. Its `### T-NNN` headings run T-000 to T-080, T-090 to T-097 and T-100 to T-112. `git grep` for T-084 and T-085 on `origin/main`, `origin/chore/r1-d-fix` and `origin/chore/tasks-v4` found nothing. The run protocol makes `TASKS.md` the source of truth for write_scope, acceptance commands and owner, so I did not invent scopes from the PR title (settings cleanup, core brief-facts endpoint). This is recorded as a conflict between the request and the sources of truth. The same happened in run r1-d-data2.

## Commands executed

- `python3.12 -m venv /tmp/venv && pip install '.[dev,streamlit,identity]'`: succeeded, PyPI is reachable.
- `git fetch origin`, `git grep` for T-084/T-085 on remote branches: no matches.
- `make static` and `make test` were not run. Only this report changed.

## Assumptions

- The IDs are mistyped or the tasks are not yet merged into `TASKS.md`.

## Changed files

- `docs/runs/RUN-r1-d2-core.md`

## Unresolved risks

- The owner must add T-084 and T-085 to `TASKS.md` or give the correct IDs before this run can be redone.
