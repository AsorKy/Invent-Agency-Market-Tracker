# Run report r1-d2-ingest

Branch: `agent/r1-d2-ingest`

## Outcome

No task was executed. All three requested tasks are blocked: T-120, T-089 and T-098.

## Reason

`TASKS.md` (v4) has no task with these IDs. Its `### T-NNN` headings run T-000 to T-080, T-090 to T-097 and T-100 to T-112. No remote branch (`origin/agent/*`, `origin/chore/*`, `origin/main`) mentions them in any file. `TASKS.md` is the source of truth for write_scope, acceptance commands and owner, so I did not invent scopes from the PR title (ingest tenant fix, core calls outside the transaction, source mocks). Per AGENTS.md this is a conflict between the request and the sources of truth that needs a human decision. The same happened in run r1-d-data2.

## Commands executed

- `python3.12 -m venv /tmp/venv` and `pip install '.[dev,streamlit,identity]'`: succeeded, so PyPI is reachable.
- `git grep` for the three IDs on `origin/main`, plus a listing of all `### T-` headings: no matches.
- `make static` and `make test` were not run, because nothing changed besides this report.

## Assumptions

- The IDs are mistyped or refer to tasks not yet merged. Candidates by topic: T-090 (tenant on the event stream) and T-092 (relay to consumers) for the ingest tenant fix. I did not choose.

## Changed files

- `docs/runs/RUN-r1-d2-ingest.md`

## Unresolved risks

- The owner must supply the correct task IDs, or add the tasks to `TASKS.md`, before this run can be redone.
