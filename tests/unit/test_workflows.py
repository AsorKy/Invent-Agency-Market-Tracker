"""Tests for the versioned n8n source workflows (REQ-ORC-001).

Checks that every versioned workflow is valid and secret free, that the source workflows keep
the trigger, budget check,
Bright Data trigger with the ingest notify URL, and notification chain, and that
the guard in ``ops/check_workflows.py`` accepts them and rejects code nodes.
"""

import json
import re
from pathlib import Path

import pytest

from libs.common.settings import Settings
from ops.check_workflows import check_directory, check_workflow

DIRECTORY = Path(__file__).resolve().parents[2] / "orchestration/n8n"
SOURCES = {
    "wf_maps_medellin": "maps",
    "wf_computrabajo_tech": "computrabajo",
    "wf_linkedin_companies_backfill": "linkedin_companies",
    "wf_linkedin_jobs": "linkedin_jobs",
    "wf_instagram_horeca": "instagram",
}
OTHERS = [
    "wf_rues_delta",
    "wf_socioeco_refresh",
    "wf_ops_health",
    "wf_hot_lead_alert",
    "wf_drift_alert",
]
EXPECTED = sorted([*SOURCES, *OTHERS])
TRIGGERS = {
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.errorTrigger",
}


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


def test_expected_workflow_files() -> None:
    assert sorted(path.stem for path in DIRECTORY.glob("*.json")) == EXPECTED


@pytest.mark.parametrize("name", EXPECTED)
def test_workflow_basics(name: str) -> None:
    document = _load(name)
    assert document["name"] == name
    types = {node["type"] for node in document["nodes"]}
    assert types & {"n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.webhook"}
    assert {node["type"] for node in document["nodes"] if "Trigger" in node["type"]} <= TRIGGERS
    names = {node["name"] for node in document["nodes"]}
    for source, targets in document["connections"].items():
        assert source in names
        assert {t["node"] for branch in targets["main"] for t in branch} <= names
    text = (DIRECTORY / f"{name}.json").read_text(encoding="utf-8")
    assert "Bearer" not in text and "password" not in text.lower()
    assert text.isascii()


@pytest.mark.parametrize("name", ["wf_rues_delta", "wf_socioeco_refresh"])
def test_schedule_workflows_end_in_notification(name: str) -> None:
    document = _load(name)
    assert _chain(document, "Schedule")[-1].startswith("Notify")
    assert _chain(document, "Error trigger")[-1].startswith("Notify")


def test_ops_health_emails_on_failure() -> None:
    document = _load("wf_ops_health")
    assert _chain(document, "Schedule") == ["Schedule", "Check core health", "Check ingest health"]
    assert _chain(document, "Error trigger") == ["Error trigger", "Email ops team"]


def test_hot_lead_alert_reads_webhook_body() -> None:
    document = _load("wf_hot_lead_alert")
    hook = next(n for n in document["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == "hot-lead"
    text = next(n for n in document["nodes"] if n["name"] == "Notify commercial team")
    body = text["parameters"]["jsonBody"]
    for field in ("company_id", "lead_id", "tenant_id", "score"):
        assert "{{$json.body." + field + "}}" in body


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


ROOT = Path(__file__).resolve().parents[2]
ENV_REFERENCE = re.compile(r"\$env\.([A-Za-z_][A-Za-z0-9_]*)")


def _defined_variables() -> set[str]:
    names = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match:
            names.add(match.group(1))
    return names | {name.upper() for name in Settings.model_fields}


def _referenced_variables() -> dict[str, set[str]]:
    return {
        path.stem: set(ENV_REFERENCE.findall(path.read_text(encoding="utf-8")))
        for path in sorted(DIRECTORY.glob("*.json"))
    }


def test_workflow_env_variables_are_defined() -> None:
    defined = _defined_variables()
    undefined = {
        name: sorted(refs - defined)
        for name, refs in _referenced_variables().items()
        if refs - defined
    }
    assert undefined == {}


def test_no_workflow_references_analytics_url() -> None:
    assert [n for n, refs in _referenced_variables().items() if "ANALYTICS_URL" in refs] == []


def test_drift_alert_webhook_requires_header_auth() -> None:
    document = _load("wf_drift_alert")
    hook = next(n for n in document["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == "drift-alert"
    assert hook["parameters"]["authentication"] == "headerAuth"
    assert set(hook["credentials"]["httpHeaderAuth"]) == {"id", "name"}
    assert _chain(document, "Drift alert event") == ["Drift alert event", "Notify data team"]
