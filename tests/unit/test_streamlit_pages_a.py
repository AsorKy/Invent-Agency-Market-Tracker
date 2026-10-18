"""Smoke-test Streamlit pages 1 to 4 with AppTest and a faked fetch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from services.streamlit.app import helpers, ui

PAGES = Path(__file__).resolve().parents[2] / "services" / "streamlit" / "pages"
BODIES: dict[str, Any] = {
    "pulse/pipeline-backlog": {
        "items": [{"stage": "new", "count": 3}],
        "as_of": "2026-10-18T12:00:00Z",
    },
    "pulse/top-sources": {
        "items": [{"source": "rues", "leads": 2}],
        "as_of": "2026-10-18T12:00:00Z",
    },
    "leads": {"items": [{"id": "1", "score": 80}], "as_of": "2026-10-18T12:00:00Z"},
    "segments": {"items": [{"segment": "a", "size": 4}], "as_of": "2026-10-18T12:00:00Z"},
    "territory/opportunity": {
        "items": [{"territory": "t1", "iop": 0.5}],
        "method_version": "v1",
        "as_of": "2026-10-18T12:00:00Z",
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


@pytest.mark.parametrize(
    "page", ["1_Market.py", "2_Leads.py", "3_Segmentos.py", "4_Territorios.py"]
)
def test_pages_render_without_exception(page: str) -> None:
    at = run(page, "analyst")
    assert not at.exception
    assert len(at.dataframe) >= 1
    assert any("Data as of" in c.value for c in at.caption)


@pytest.mark.parametrize("page", ["1_Market.py", "2_Leads.py"])
def test_pages_block_without_role(page: str) -> None:
    at = run(page, None)
    assert not at.exception
    assert any(helpers.NO_ACCESS in i.value for i in at.info)
    assert len(at.dataframe) == 0


def test_dev_role_env_grants_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAMLIT_DEV_ROLE", "analyst")
    at = run("1_Market.py", None)
    assert len(at.dataframe) >= 1


def test_territorios_shows_ioz_placeholder() -> None:
    at = run("4_Territorios.py", "strategist")
    assert any("IOZ" in i.value and "L3" in i.value for i in at.info)


def test_failed_card_shows_error_and_retry() -> None:
    backup = dict(BODIES)
    BODIES.pop("segments")
    try:
        at = run("3_Segmentos.py", "admin")
    finally:
        BODIES.update(backup)
    assert [e.value for e in at.error] == [helpers.GENERIC_ERROR]
    assert any(b.label == "Retry" for b in at.button)
