"""Guard the frontend design components against mock data and run their offline Node checks.

``frontend/`` holds the dc design components that docs/runbooks/frontend.md describes. Every
list, KPI and chart there must come from the API, so the first test fails when a seed function
or a demo literal of the original design export comes back. The second runs
``frontend/tests`` (node's built-in runner: frontend/api.js against a fake fetch, and every
component mounted against fixtures) and is skipped when node is not installed. Files are
listed with ``git ls-files``, never with a directory walk.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# The style guide board shows components with sample text; it is not served as an app view.
REFERENCE_ONLY = {"frontend/Design System.dc.html"}
MOCK_MARKERS = (
    "Empresa Demo",
    "Cliente Demo",
    "usuario@invent.example",
    "seedLeads",
    "seedMatches",
    "seedProjects",
    "seedCharters",
    "'BD rep 1'",
    "Simular conflicto",
)
NODE_TESTS = ("frontend/tests/api.test.js", "frontend/tests/components.test.js")


def frontend_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "frontend"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def test_app_files_carry_no_mock_data() -> None:
    files = frontend_files()
    app = [
        name
        for name in files
        if name.count("/") == 1
        and (name.endswith(".dc.html") or name == "frontend/api.js")
        and name not in REFERENCE_ONLY
    ]
    assert "frontend/Invent Market Tracker.dc.html" in app
    assert "frontend/DataView.dc.html" in app
    found = [
        f"{name}: {marker}"
        for name in app
        for marker in MOCK_MARKERS
        if marker in (ROOT / name).read_text(encoding="utf-8")
    ]
    assert found == []


def test_app_loads_the_data_layer_before_the_dc_runtime() -> None:
    page = (ROOT / "frontend" / "Invent Market Tracker.dc.html").read_text(encoding="utf-8")
    assert page.index('src="./api.js"') < page.index('src="./support.js"')


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_frontend_checks_pass() -> None:
    files = set(frontend_files())
    assert set(NODE_TESTS) <= files
    result = subprocess.run(
        ["node", "--test", *NODE_TESTS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
