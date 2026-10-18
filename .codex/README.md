# Codex agent profiles

This directory contains durable role profiles for the Invent Market Tracker development workflow. The profiles define responsibilities, preferred paths, write boundaries, collaboration rules, and a color used to identify each role in deployment summaries.

These files are reusable role instructions. They do not create production microservices and they do not replace the backend orchestration layer. The coordinator must load the requested profile before delegating work.

## Deploy a role

Ask the coordinator with a bounded task, for example:

```text
Deploy `lead-backend-engineer` using `.codex/agents/lead-backend-engineer.md`.
Review the service dependency graph and repair import or endpoint wiring issues.
Write only to services/core, services/gateway, libs/common, and libs/contracts.
Return changed files, validation commands, results, and unresolved risks.
```

For several agents, define disjoint write scopes:

```text
Deploy these profiles in parallel:

- `architecture-reviewer`: read-only architecture and dependency review.
- `database-engineer`: migrations and repository query review.
- `qa-reviewer`: tests and validation review.

Do not edit the same files concurrently. The coordinator integrates cross-cutting changes.
```

## Files

- `agent-registry.yaml`: machine-readable role catalog, responsibilities, paths, and colors.
- `agents/*.md`: detailed instructions for each role.

## Collaboration rules

- Keep tasks independent and bounded.
- Treat shared configuration and shared contracts as coordinator-owned unless explicitly assigned.
- Use read-only review roles when the task is analysis only.
- Ask agents to report findings with file paths and line references where possible.
- Review all edits in the main context before considering the task complete.
