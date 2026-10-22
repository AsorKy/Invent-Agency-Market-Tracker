"""Test the Validation page and the Ops V6 card with AppTest and a faked fetch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from services.streamlit.app import helpers, ui

PAGES = Path(__file__).resolve().parents[2] / "services" / "streamlit" / "pages"
AS_OF = "2026-10-18T12:00:00Z"
META = {"measured_at": "2026-10-01T00:00:00Z", "source_version": "sv-9", "psi": 0.4}
BODIES: dict[str, Any] = {
    "projects": {"data": [{"id": "p1"}], "as_of": AS_OF},
    "validation/projects/p1/financial": {
        "measured_at": "m-v1",
        "source_version": "s-v1",
        "irr": 0.3,
    },
    "validation/projects/p1/unit-economics": {
        "measured_at": "m-v2",
        "source_version": "s-v2",
        "ltv_cac_ratio": 0.5,
    },
    "validation/projects/p1/funnel": [
        {"measured_at": "m-v3", "source_version": "s-v3", "coverage": 3}
    ],
    "validation/projects/p1/growth": {"measured_at": "m-v4", "source_version": "s-v4", "nrr": 1.1},
    "validation/projects/p1/portfolio": {
        "measured_at": "m-v5",
        "source_version": "s-v5",
        "hhi": 0.2,
    },
    "validation/projects/p1/meta-ml": META,
    "validation/meta-ml": META,
    "pulse/ops/health": {"sources": [], "outbox_backlog": 1, "as_of": AS_OF},
}


@pytest.fixture(autouse=True)
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    bodies = dict(BODIES)

    def fetch(url: str, timeout: float) -> tuple[int, dict[str, str], Any]:
        path = url.split("/api/v1/", 1)[1].split("?", 1)[0]
        if path not in bodies:
            return 404, {}, None
        return 200, {}, bodies[path]

    real = helpers.safe_fetch
    monkeypatch.setattr(helpers, "safe_fetch", lambda url: real(url, fetch=fetch))
    ui.cached_fetch.clear()
    return bodies


def run(page: str) -> AppTest:
    at = AppTest.from_file(str(PAGES / page), default_timeout=20)
    at.session_state["role"] = "analyst"
    return at.run()


def texts(at: AppTest) -> str:
    return "\n".join(
        [m.value for m in at.markdown]
        + [c.value for c in at.caption]
        + [s.value for s in at.subheader]
    )


def test_all_six_cards_render_with_metadata() -> None:
    at = run("9_Validation.py")
    assert not at.exception
    body = texts(at)
    for title in (
        "V1 Financial",
        "V2 Unit economics",
        "V3 Funnel",
        "V4 Growth",
        "V5 Portfolio",
        "V6 Meta-ML",
    ):
        assert title in body
    for n in range(1, 6):
        assert f"measured_at: m-v{n} | source_version: s-v{n}" in body
    assert "irr: 0.3 [healthy]" in body
    assert "ltv_cac_ratio: 0.5 [kill]" in body
    assert not at.warning


def test_one_failing_card_degrades_alone(fake_fetch: dict[str, Any]) -> None:
    del fake_fetch["validation/projects/p1/growth"]
    fake_fetch["validation/projects/p1/funnel"] = {}
    at = run("9_Validation.py")
    assert not at.exception
    assert len(at.error) == 1
    assert any("Degraded" in w.value for w in at.warning)
    body = texts(at)
    assert "measured_at: m-v1" in body and "measured_at: m-v5" in body


def test_no_projects_degrades(fake_fetch: dict[str, Any]) -> None:
    del fake_fetch["projects"]
    at = run("9_Validation.py")
    assert not at.exception
    assert any("no projects" in w.value for w in at.warning)


def test_ops_shows_v6() -> None:
    at = run("7_Ops.py")
    assert not at.exception
    body = texts(at)
    assert "V6 Meta-ML" in body
    assert "psi: 0.4 [kill]" in body or "psi: 0.4 [watch]" in body
    assert "source_version: sv-9" in body
