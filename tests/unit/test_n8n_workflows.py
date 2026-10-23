"""Tests for the wf_socioeco_refresh workflow authentication (D-33, T-185)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "orchestration/n8n"


def _workflow() -> dict:
    return json.loads((DIRECTORY / "wf_socioeco_refresh.json").read_text(encoding="ascii"))


def _nodes() -> dict:
    return {node["name"]: node for node in _workflow()["nodes"]}


def test_workflow_stays_inactive() -> None:
    assert _workflow()["active"] is False


def test_first_node_mints_a_token_with_the_dedicated_credential() -> None:
    document = _workflow()
    nodes = _nodes()
    mint = nodes["Mint pulse token"]
    assert mint["parameters"]["url"] == (
        "={{$env.IDENTITY_URL}}/internal/identity/mint/socioeco"
    )
    assert mint["credentials"]["httpHeaderAuth"] == {
        "id": "socioeco-mint",
        "name": "socioeco-mint-credential",
    }
    assert document["connections"]["Schedule"]["main"][0][0]["node"] == "Mint pulse token"
    follow = document["connections"]["Mint pulse token"]["main"][0][0]["node"]
    assert follow == "Refresh socioeconomic data"


def test_refresh_node_sends_the_minted_token() -> None:
    refresh = _nodes()["Refresh socioeconomic data"]
    assert refresh["parameters"]["url"].startswith("={{$env.PULSE_URL}}/internal/pulse/")
    assert "credentials" not in refresh
    assert refresh["parameters"]["specifyHeaders"] == "json"
    assert refresh["parameters"]["jsonHeaders"] == (
        '={{ {"X-Service-Token": $json.access_token} }}'
    )


def test_internal_service_token_never_appears_in_n8n_files() -> None:
    for path in DIRECTORY.iterdir():
        if path.is_file():
            assert "INTERNAL_SERVICE_TOKEN" not in path.read_text(encoding="ascii"), path.name
