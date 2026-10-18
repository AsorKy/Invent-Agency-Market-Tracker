# Repository agent instructions

## Project context

Invent Market Tracker is a data-driven platform for evaluating Colombian market opportunities, tracking potential leads, and deciding whether proposed projects should proceed. The platform integrates Bright Data based ingestion, domain services, analytics, machine learning workflows, and operational automation.

The frontend is currently out of scope unless a task explicitly requests it.

## Language and documentation

- Write source code, comments, docstrings, commit messages, and repository documentation in English.
- Exception: the ADRs in `docs/adr/` stay in Spanish. `ops/check_language.py` skips that folder only.
- Add a module docstring at the beginning of every executable Python script.
- Keep role-specific agent instructions in `.codex/agents/`.
- Read the relevant architecture documentation before changing service boundaries or data contracts.

## Agent coordination

- `.codex/agent-registry.yaml` is the role catalog and color map.
- Load the requested profile from `.codex/agents/` before assigning work to a role.
- Give every delegated task a bounded scope, an expected output, and explicit write permissions.
- Prefer parallel work only when agents have independent questions or disjoint write sets.
- Do not let multiple agents edit the same file concurrently.
- The coordinator owns cross-cutting changes to `pyproject.toml`, `.env.example`, `docker-compose.yml`, migrations, shared contracts, and CI configuration unless a task explicitly assigns ownership.
- Agents must report changed files, commands executed, validation results, assumptions, and unresolved risks.

## Quality gates

Run the repository's static checks after implementation changes. At minimum, use `make static` when the environment supports it and report any unavailable external dependency or service.

Do not add frontend implementation while the frontend remains out of scope.

## Autonomous run protocol

This section applies to unattended runs, for example a Claude Managed Agents session working on a branch while the owner is away. It extends the sections above and never overrides them.

### Source of truth

1. The ADRs (ADR-IMT-0001, ADR-IMT-0002, ADR-IMT-0003) win over everything else. They live in `docs/adr/`, are written in Spanish, and are exported from the project vault: never edit them. `docs/README.md` indexes them by topic.
2. `SPEC.md` states the requirements derived from the ADRs, each with a stable ID.
3. `TASKS.md` lists executable work. Every task cites the requirement IDs it satisfies.
4. Code and tests come last. If code contradicts `SPEC.md`, fix the code or record a conflict. Do not edit `SPEC.md` to match the code.

If two sources disagree, stop work on that point and record the conflict in the run report instead of choosing silently.

### Git rules

- Work only on the branch `agent/<run-id>`. Never commit to `main`.
- One task is one commit. Commit message format: `T-NNN short summary`.
- Push after every task. Work that is not pushed is lost when the session ends.
- Never force-push, rewrite history, or delete branches.

### Task loop

1. Read this file, then only the `SPEC.md` sections named in the task `spec` field, then only the files listed in the task `context` field.
2. Take the first task with `status: todo` whose `depends` entries are all `done`. Set it to `in_progress`.
3. Implement inside the task `write_scope` only.
4. Run the task `acceptance` commands and `make static`.
5. On success set `status: done` and add an `evidence:` line with the command and its result. After three focused failed attempts set `status: blocked` with the reason and move to the next task.
6. Commit, push, and repeat.

### Sandbox assumptions

- Assume no Docker daemon, no Supabase, no Bright Data, no RUES, and no real secrets. Use `services/mocks` and `datagen` instead.
- Never print, log, or commit secrets. Only edit `.env.example`, never `.env`.
- Network access is limited to package registries and GitHub. If a task needs anything else, mark it `blocked`.

### Stop conditions

- Every task is `done` or `blocked`.
- The session budget is reached.
- A conflict between sources of truth needs a human decision.

### Token economy

- Do not re-read whole ADRs. Use the requirement text in `SPEC.md` and search the ADR for the cited section only.
- Prefer targeted search over reading large files. Keep command output short.
- Keep commits small so an interrupted run loses little.

### Run report

At the end of a run write `docs/runs/RUN-<run-id>.md` with: tasks done and blocked, commands executed with results, assumptions, changed files, and unresolved risks.

## Agent roster

Role profiles live in `.codex/agents/` and are cataloged in `.codex/agent-registry.yaml`. Claude Code subagent definitions will live in `.claude/agents/<id>.md`. Until they exist, the `.codex/agents/` profiles are the role definitions. Keep the registry and both directories in sync.

| Role | Mode | Owns | Profile |
| --- | --- | --- | --- |
| coordinator (root agent) | integration | shared paths listed in the registry | this file |
| lead-backend-engineer | implementation | services/core, services/gateway, libs/common, libs/contracts | `.codex/agents/lead-backend-engineer.md` |
| second-backend-engineer | implementation | services/ingest, services/analytics, services/ml, transform, orchestration/n8n | `.codex/agents/second-backend-engineer.md` |
| database-engineer | implementation and review | migrations, ops/seed, repository modules, libs/common/db.py | `.codex/agents/database-engineer.md` |
| devops-engineer | implementation and review | deploy, .github, docker-compose.yml, .env.example, Makefile, ops | `.codex/agents/devops-engineer.md` |
| qa-reviewer | review and test | tests, ops/check_*.py | `.codex/agents/qa-reviewer.md` |
| architecture-reviewer | review | docs, .codex | `.codex/agents/architecture-reviewer.md` |
| frontend-engineer | implementation | frontend | `.codex/agents/frontend-engineer.md` (out of scope) |

Ownership of the services that were not in the registry is settled (SPEC decision D-04): lead-backend-engineer owns `identity` and `brief`; second-backend-engineer owns `pulse` and `streamlit`; qa-reviewer owns `services/mocks`. `services/analytics` is merged into `services/pulse` (REQ-ARC-001). Add these owners to `agent-registry.yaml` (REQ-ARC-002).

### Session modes

- F0 runs use a single agent that acts as coordinator and takes one role at a time per task, following the task `owner` field.
- Multiagent runs start only after `libs/contracts` is frozen. Use one coordinator plus one agent per service group, with disjoint write sets and at most three concurrent agents, matching `default_max_concurrent_agents` in the registry. Managed Agents allows one delegation level only.
