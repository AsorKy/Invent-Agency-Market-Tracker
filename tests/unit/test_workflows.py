"""Tests for the versioned n8n source workflows (REQ-ORC-001).

Checks that the maps and computrabajo workflows keep the trigger, budget check,
Bright Data trigger with the ingest notify URL, and notification chain, and that
the guard in ``ops/check_workflows.py`` accepts them and rejects code nodes.
"""

import json
from pathlib import Path

import pytest

from ops.check_workflows import check_directory, check_workflow

DIRECTORY = Path(__file__).resolve().parents[2] / "orchestration/n8n"
SOURCES = {"wf_maps_medellin": "maps", "wf_computrabajo_tech": "computrabajo"}


def _load(name: str) -> dict:
    return json.loads((DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))


def _chain(document: dict, start: str) -> list[str]:
    order, current = [start], start
    while current in document["connections"]:
        current = document["connections"][current]["main"][0][0]["node"]
        order.append(current)
    return order


def test_all_workflows_pass_guard() -> None:
    assert check_directory(DIRECTORY) == {}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_source_workflow_chain(name: str) -> None:
    document = _load(name)
    assert document["name"] == name
    assert document["versionId"] and document["meta"]["payloadVersion"]
    assert _chain(document, "Schedule") == [
        "Schedule",
        "Check ingest budget",
        "Trigger Bright Data",
        "Notify run started",
    ]
    assert _chain(document, "Error trigger") == ["Error trigger", "Notify run failed"]


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_budget_check_and_notify_url(name: str) -> None:
    nodes = {node["name"]: node for node in _load(name)["nodes"]}
    budget = nodes["Check ingest budget"]["parameters"]["url"]
    assert budget.startswith("={{$env.INGEST_URL}}/internal/ingest/budget/")
    assert budget.endswith("/" + SOURCES[name])
    trigger = nodes["Trigger Bright Data"]["parameters"]["jsonBody"]
    assert "{{$env.INGEST_WEBHOOK_URL}}" in trigger
    assert json.loads(trigger[1:].replace("{{$env.INGEST_WEBHOOK_URL}}", "x"))["notify"] == "x"


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_credentials_by_name_only(name: str) -> None:
    text = (DIRECTORY / f"{name}.json").read_text(encoding="utf-8")
    for node in _load(name)["nodes"]:
        for credential in node.get("credentials", {}).values():
            assert set(credential) == {"id", "name"}
    assert "Bearer" not in text


def test_guard_rejects_code_node() -> None:
    document = _load("wf_maps_medellin")
    document["nodes"].append({"name": "x", "type": "n8n-nodes-base.code"})
    assert check_workflow(document)
