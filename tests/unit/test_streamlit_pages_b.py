"""Smoke-test Streamlit pages 5 to 7 with AppTest and a faked fetch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from services.streamlit.app import helpers, ui

PAGES = Path(__file__).resolve().parents[2] / "services" / "streamlit" / "pages"
AS_OF = "2026-10-18T12:00:00Z"
BODIES: dict[str, Any] = {
    "pipeline/kanban": {
        "as_of": AS_OF,
        "columns": {"new": [{"id": "l1", "stage": "new"}], "won": []},
    },
    "projects": {"data": [{"id": "p1", "status": "open", "roi": 1.2}], "as_of": AS_OF},
    "pulse/ops/health": {
        "sources": [
            {"source": "rues", "as_of": 1760788800000, "stale": False},
            {"source": "serp", "as_of": None, "stale": True},
        ],
        "outbox_backlog": 7,
        "as_of": AS_OF,
    },
}


@pytest.fixture(autouse=True)
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(url: str, timeout: float) -> tuple[int, dict[str, str], Any]:
        path = url.split("/api/v1/", 1)[1].split("?", 1)[0]
        if path not in BODIES:
            return 404, {}, None
        return 200, {}, BODIES[path]

    real = helpers.safe_fetch
    monkeypatch.setattr(helpers, "safe_fetch", lambda url: real(url, fetch=fetch))
    ui.cached_fetch.clear()


def run(page: str, role: str | None) -> AppTest:
    at = AppTest.from_file(str(PAGES / page), default_timeout=20)
    if role:
        at.session_state["role"] = role
    return at.run()


def test_pipeline_renders_columns() -> None:
    at = run("5_Pipeline.py", "bd_rep")
    assert not at.exception
    assert any("new" in m.value for m in at.markdown)
    assert any("Data as of" in c.value for c in at.caption)


def test_projects_renders_table_and_export_gate() -> None:
    at = run("6_Proyectos.py", "analyst")
    assert not at.exception
    assert len(at.dataframe) == 1
    assert run("6_Proyectos.py", "bd_rep").exception == []


def test_ops_renders_sources_and_backlog() -> None:
    at = run("7_Ops.py", "admin")
    assert not at.exception
    assert any(m.label == "Outbox backlog" and m.value == "7" for m in at.metric)
    assert len(at.dataframe) == 1
    assert any("stale" in w.value for w in at.warning)


@pytest.mark.parametrize("page", ["5_Pipeline.py", "6_Proyectos.py", "7_Ops.py"])
def test_pages_block_without_role(page: str) -> None:
    at = run(page, None)
    assert not at.exception
    assert any(helpers.NO_ACCESS in i.value for i in at.info)
    assert len(at.dataframe) == 0


def test_ops_blocks_intake_role() -> None:
    at = run("7_Ops.py", "intake")
    assert any(helpers.NO_ACCESS in i.value for i in at.info)


def test_failed_card_shows_error_and_retry() -> None:
    backup = dict(BODIES)
    BODIES.pop("projects")
    try:
        at = run("6_Proyectos.py", "admin")
    finally:
        BODIES.update(backup)
    assert [e.value for e in at.error] == [helpers.GENERIC_ERROR]
    assert any(b.label == "Retry" for b in at.button)
