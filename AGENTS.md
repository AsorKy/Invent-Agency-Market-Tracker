# Repository agent instructions

## Project context

Invent Market Tracker is a data-driven platform for evaluating Colombian market opportunities, tracking potential leads, and deciding whether proposed projects should proceed. The platform integrates Bright Data based ingestion, domain services, analytics, machine learning workflows, and operational automation.

The frontend is currently out of scope unless a task explicitly requests it.

## Language and documentation

- Write source code, comments, docstrings, commit messages, and repository documentation in English.
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
