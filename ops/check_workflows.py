"""Check that versioned n8n workflow JSON is orchestration only and secret free.

The command reads every ``*.json`` file under ``orchestration/n8n/`` and fails on
code nodes, on node types outside a transport-only allowlist, and on secret-like
literal values. It relates to ``Makefile``, CI, and REQ-ORC-001 and REQ-SEC-004.
The allowlist holds triggers (webhook, schedule, cron, manual, error), HTTP calls,
webhook responses, and notification senders (email, Slack, Telegram). These only
move data between systems. Code, function, if, switch, set, merge, and similar
nodes can compute business values, so they are rejected and that logic belongs in
the Python services. Credentials must come from ``$env`` expressions.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

WORKFLOW_DIR = Path(__file__).resolve().parents[1] / "orchestration/n8n"
ALLOWED_NODE_TYPES = frozenset(
    {
        "n8n-nodes-base.webhook",
        "n8n-nodes-base.scheduleTrigger",
        "n8n-nodes-base.cron",
        "n8n-nodes-base.manualTrigger",
        "n8n-nodes-base.errorTrigger",
        "n8n-nodes-base.httpRequest",
        "n8n-nodes-base.respondToWebhook",
        "n8n-nodes-base.emailSend",
        "n8n-nodes-base.slack",
        "n8n-nodes-base.telegram",
        "n8n-nodes-base.noOp",
    }
)
CODE_NODE_TYPES = frozenset(
    {"n8n-nodes-base.code", "n8n-nodes-base.function", "n8n-nodes-base.functionItem"}
)
SECRET_KEY = re.compile(r"(authorization|password|passwd|secret|token|api[-_]?key)", re.I)
SECRET_VALUE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bsk-[A-Za-z0-9_-]{20,}|\bBearer\s+[A-Za-z0-9._-]{16,})"
)


def _walk(value: Any, key: str = "") -> list[str]:
    """Return problem codes for secret-like literals below one JSON value."""
    problems: list[str] = []
    if isinstance(value, dict):
        name = str(value.get("name", ""))
        if SECRET_KEY.search(name) and isinstance(value.get("value"), str):
            problems.extend(_check_literal(value["value"]))
        for child_key, child in value.items():
            if child_key == "value" and SECRET_KEY.search(name):
                continue
            problems.extend(_walk(child, str(child_key)))
    elif isinstance(value, list):
        for child in value:
            problems.extend(_walk(child, key))
    elif isinstance(value, str):
        if SECRET_KEY.search(key):
            problems.extend(_check_literal(value))
        elif SECRET_VALUE.search(value) and "$env" not in value:
            problems.append("secret-like value")
    return problems


def _check_literal(text: str) -> list[str]:
    if text and "$env" not in text:
        return ["secret-like literal in credential field"]
    return []


def check_workflow(document: Any) -> list[str]:
    """Return human-readable problems for one parsed workflow document."""
    if not isinstance(document, dict) or not isinstance(document.get("nodes"), list):
        return ["workflow has no nodes list"]
    problems: list[str] = []
    for node in document["nodes"]:
        node_type = str(node.get("type", "")) if isinstance(node, dict) else ""
        label = str(node.get("name", "?")) if isinstance(node, dict) else "?"
        if node_type in CODE_NODE_TYPES:
            problems.append(f"node '{label}': code node {node_type}")
        elif node_type not in ALLOWED_NODE_TYPES:
            problems.append(f"node '{label}': type {node_type or '?'} is not transport-only")
    problems.extend(f"{code}" for code in _walk(document))
    return problems


def check_directory(directory: Path) -> dict[str, list[str]]:
    """Check every workflow JSON file in a directory; map file name to problems."""
    results: dict[str, list[str]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            results[path.name] = ["file is not valid JSON"]
            continue
        problems = check_workflow(document)
        if problems:
            results[path.name] = problems
    return results


def main() -> int:
    failures = check_directory(WORKFLOW_DIR)
    for name, problems in failures.items():
        for problem in problems:
            print(f"{name}: {problem}", file=sys.stderr)
    if failures:
        return 1
    print(f"Workflow guard: OK ({len(list(WORKFLOW_DIR.glob('*.json')))} workflows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
