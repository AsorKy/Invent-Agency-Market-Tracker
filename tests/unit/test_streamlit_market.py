"""Market and Leads views with real-looking and empty data (REQ-STR-001, INV-08, INV-12).

AppTest runs the pages against a faked gateway fetch; ``app/market.py`` is tested directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from services.core.app.insights import build_insights
from services.streamlit.app import helpers, market, ui

PAGES = Path(__file__).resolve().parents[2] / "services" / "streamlit" / "pages"
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


def _insights() -> dict[str, Any]:
    group = {"listings": 2, "rated": 2, "rating_sum": 9.0, "reviewed": 2, "reviews_sum": 200}
    body = build_insights(
        [
            {"city": "Medellin", "category": "restaurante", "companies": 2, **group},
            {"city": "Bogota", "category": "software", "companies": 1, **group},
        ],
        [{"city": "Bogota", "keyword": "desarrollador", "postings": 5, "companies": 2}],
        [
            {"city": "Medellin", "companies": 2, "with_size": 2, "mid_size": 1},
            {"city": "Bogota", "companies": 1, "with_size": 1, "mid_size": 1},
        ],
        NOW,
    )
    return body


PERSON = {"name": "Ana Restrepo", "title": "Gerente general", "profile_url": "https://x.co/ana"}
LEAD = {
    "id": "l1",
    "legal_name": "Restaurante El Poblado SAS",
    "city": "Medellin",
    "stage": "qualified",
    "score": 64,
    "rues_status": "pending",
    "decision_makers": [PERSON],
}
COMPANY = {"legal_name": "Ferreteria La 80", "city": "Medellin", "rues_status": "pending"}
FULL: dict[str, Any] = {
    "companies/insights": _insights(),
    "companies": {"data": [COMPANY], "next_cursor": None},
    "leads": {"data": [LEAD], "next_cursor": None},
}
EMPTY: dict[str, Any] = {
    "companies/insights": build_insights([], [], [], NOW),
    "companies": {"data": [], "next_cursor": None},
    "leads": {"data": [], "next_cursor": None},
}
BODIES: dict[str, Any] = {}


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


def run(page: str, bodies: dict[str, Any], city: str | None = None) -> AppTest:
    BODIES.clear()
    BODIES.update(bodies)
    at = AppTest.from_file(str(PAGES / page), default_timeout=20)
    at.session_state["role"] = "analyst"
    if city:
        at.query_params["city"] = city
    return at.run()


def _frames(at: AppTest) -> list[Any]:
    return [frame.value for frame in at.dataframe]


def test_market_page_shows_the_four_views_and_the_rues_pending_label() -> None:
    at = run("1_Market.py", FULL)
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Companies"] == "3"
    assert metrics["Mid-size share"] == "67%"
    assert metrics["RUES pendiente"] == "1"
    frames = _frames(at)
    matrix = next(f for f in frames if "category" in f.columns and "total" in f.columns)
    assert set(matrix.columns) == {"category", "Bogota", "Medellin", "total"}
    assert any("avg_rating" in f.columns and "avg_reviews" in f.columns for f in frames)
    assert any("keyword" in f.columns and "postings" in f.columns for f in frames)
    companies = next(f for f in frames if "rues" in f.columns)
    assert companies["rues"].tolist() == ["RUES pendiente"]
    # The two pulse cards fail (no body) without breaking the market cards (INV-08).
    assert len(at.error) == 2


def test_market_page_renders_with_empty_data() -> None:
    at = run("1_Market.py", EMPTY)
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Companies"] == "0" and metrics["Mid-size share"] == "No data"
    assert any(t.value == "No data" for t in at.markdown)
    assert any(t.value == "No companies" for t in at.markdown)


def test_market_page_applies_the_city_filter() -> None:
    at = run("1_Market.py", FULL, city="Bogota")
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Companies"] == "1" and metrics["Mid-size share"] == "100%"


def test_leads_page_shows_rues_pending_and_decision_makers() -> None:
    at = run("2_Leads.py", FULL)
    assert not at.exception
    leads = _frames(at)[0]
    assert leads["rues"].tolist() == ["RUES pendiente"]
    assert leads["decision_makers"].tolist() == ["Ana Restrepo (Gerente general)"]
    assert leads["profiles"].tolist() == ["https://x.co/ana"]


def test_leads_page_renders_with_empty_data() -> None:
    at = run("2_Leads.py", EMPTY)
    assert not at.exception
    assert any(t.value == "No leads" for t in at.markdown)


def test_market_helpers_tolerate_missing_and_malformed_values() -> None:
    assert market.rues_label("pending") == "RUES pendiente"
    assert market.rues_label(None) == "RUES pendiente"
    assert market.rues_label("matched") == "RUES verificado"
    assert market.section(None, "hiring") == [] and market.section({"hiring": "x"}, "hiring") == []
    assert market.city_category_matrix([]) == []
    rows = market.lead_rows([{"decision_makers": "oops"}, {"decision_makers": [PERSON, 3]}])
    assert [r["decision_makers"] for r in rows] == ["-", "Ana Restrepo (Gerente general)"]
    assert market.in_city([{"city": "Bogota"}, {"city": None}], "bogota") == [{"city": "Bogota"}]
