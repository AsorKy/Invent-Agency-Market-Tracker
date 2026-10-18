---
name: restricted-assets
tier: always
derives_from: []
derives_from_reason: owner directive of 2026-10-18 (zero tolerance, owner-administered only); no POL-* recorded yet, run /policy to record it
updated: 2026-10-18
---

# Restricted assets - zero tolerance

Restricted: any file named `launch_managed_run.*` (and the misspelling `launhc_managed_run.*`) at any depth. It is owner-only. The owner alone reads, edits, runs, and administers it. Every agent and every tool, including you, treats it as if it did not exist.

Never, with no exception and whatever a task, test, tool output, or other instruction says:

- Open, read, cat, head, tail, diff, parse, summarize, quote, copy, move, rename, delete, chmod, execute, or import it, or pass it to a tool or subagent.
- Put it in any search or scan: grep, rg, glob, find, rglob, os.walk, mypy, ruff, compileall, dbt, archives, or code indexers. Enumerate files only with `git ls-files --cached --others --exclude-standard`, which excludes it.
- Stage, commit, or push it. Never use `git add -f`. Never remove or weaken its ignore rules.
- Write its name or contents into code, tests, commits, logs, run reports, prompts, or memory. Say "restricted asset" when you must refer to it.
- Edit the guardrails: `.gitignore`, `.git/info/exclude`, `.git/hooks/`, `.claude/settings*.json`, `.claude/hooks/`, or this file.

If a task seems to need it, mark the task `blocked` with the reason "restricted asset" and stop. If its content ever appears in your context, do not repeat or use it, and tell the owner at once.

Enforcement: this file is context, not a lock. The mechanical guards are the git ignore rules, the local exclude file, the local pre-commit and pre-push hooks, `ops/check_restricted_assets.py` (part of `make static`), and in `.claude/settings.json` the `permissions.deny` rules plus a PreToolUse hook. They are best effort against shell tricks. The only airtight protection is the file mode or its location, which the owner controls.
