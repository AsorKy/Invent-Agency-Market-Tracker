"""PreToolUse guard: deny any tool call that could touch a restricted owner-only asset.

The hook reads the tool call as JSON on stdin and exits with code 2 (deny) when the
call names a restricted file, expands a root-level wildcard over it, runs a recursive
search that ignores .gitignore, force-adds ignored files, or edits the guardrail
files themselves. It fails closed: malformed input is denied. It never opens the
restricted file and never echoes the matched text. Relates to
``.claude/settings.json``, ``.claude/conventions/restricted-assets.md``, and
``ops/check_restricted_assets.py``. It is best effort against shell tricks.
"""

import fnmatch
import json
import re
import shlex
import sys

RESTRICTED_NAMES = ("launch_managed_run.py", "launhc_managed_run.py")
STEMS = ("launch_managed_run", "launhc_managed_run", "managed_run")
GUARD_PATHS = (
    ".claude/settings",
    ".claude/hooks",
    ".claude/conventions/restricted-assets",
    ".git/hooks",
    ".git/info/exclude",
)
WRITE_VERBS = re.compile(
    r"(>|\btee\b|\bsed\b|\brm\b|\bmv\b|\bcp\b|\bchmod\b|\bchown\b|\bperl\b|\bpython3?\b|\bnode\b"
    r"|\btruncate\b|\bdd\b|\bsponge\b|\binstall\b|\bgit\s+(config|update-index|checkout|restore)\b)"
)
RISKY_COMMANDS = (
    (re.compile(r"\b[efz]?grep\b[^|;&]*\s(-[a-zA-Z]*[rR]|--recursive|--dereference-recursive)"),
     "recursive grep ignores .gitignore; use rg or git grep"),
    (re.compile(r"\bfind\b[^|;&]*(-exec|-ok|-fprint|-print0)|\bxargs\b"),
     "find with actions or xargs can read ignored files"),
    (re.compile(r"\brg\b[^|;&]*(\s-u+|--no-ignore|--hidden)"), "rg must keep its ignore rules"),
    (re.compile(r"\bgit\s+add\b[^|;&]*(\s-f\b|--force)"), "never force-add ignored files"),
    (re.compile(r"\bgit\s+grep\b[^|;&]*(--untracked|--no-exclude-standard)"),
     "git grep must keep its ignore rules"),
    (re.compile(r"\b(mypy|compileall|black|flake8|pyflakes|pylint|bandit|isort)\b[^|;&]*\s\.(\s|/|$)"),
     "root-wide Python tooling would open ignored files"),
)
DENY = "BLOCKED by the zero-tolerance guard for restricted assets: {why}. Do not retry or work around it; report it to the owner."


def deny(why: str) -> None:
    print(DENY.format(why=why), file=sys.stderr)
    raise SystemExit(2)


def squash(text: str) -> str:
    return re.sub(r"[\s\\'\"`+,\[\]()$]", "", text).lower()


def root_wildcard_hits(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if not any(char in token for char in "*?["):
            continue
        cleaned = token[2:] if token.startswith("./") else token
        if "/" in cleaned:
            continue
        if any(fnmatch.fnmatch(name, cleaned) for name in RESTRICTED_NAMES):
            return True
    return False


def main() -> None:
    try:
        call = json.loads(sys.stdin.read())
        tool = str(call.get("tool_name", ""))
        tool_input = call.get("tool_input", {})
        blob = json.dumps(tool_input, ensure_ascii=True)
    except (ValueError, AttributeError):
        deny("the hook could not parse the tool call")
    flat = squash(blob)
    if any(stem in flat for stem in STEMS):
        deny("the call names a restricted asset")
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if root_wildcard_hits(command):
            deny("a root-level wildcard would expand over a restricted asset; list files explicitly")
        for pattern, why in RISKY_COMMANDS:
            if pattern.search(command):
                deny(why)
        if any(path in command for path in GUARD_PATHS) and WRITE_VERBS.search(command):
            deny("only the owner may change the guardrail files")
    elif tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        target = str(tool_input.get("file_path", tool_input.get("notebook_path", "")))
        if any(path in target for path in GUARD_PATHS):
            deny("only the owner may change the guardrail files")


if __name__ == "__main__":
    main()
