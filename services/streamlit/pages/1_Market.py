"""Render the Market radar page: pulse KPIs and the market view of the loaded companies.

Each card loads independently through ``ui.card``. The pulse cards read ``pulse/*``; the market
cards read ``companies/insights`` (one cached call for the four cards: companies by city and
category, Maps rating and reviews, hiring demand by city and keyword, mid-size share) and the
company list, where a company without a RUES match shows ``RUES pendiente``. When the tenant
has no Maps signal (``maps_available`` false) the ratings card says ``Maps no disponible``.
The global city filter narrows the market rows. It relates to ``_common.py``, ``app/ui.py``
and ``app/market.py``.
"""

from typing import Any

import streamlit as st

from services.streamlit.app import market, ui
from services.streamlit.pages import _common

filters = _common.start_page("Market")
INSIGHTS_URL = _common.api_url("companies/insights", filters)


def _kpis(data: Any) -> None:
    rows = _common.rows_of(data)
    total = sum(float(r.get("count") or 0) for r in rows)
    cols = st.columns(2)
    cols[0].metric("Pipeline stages", len(rows))
    cols[1].metric("Leads in backlog", int(total))
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.write("No data")


def _sources(data: Any) -> None:
    rows = _common.rows_of(data)
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.write("No data")


def _table(rows: list[dict[str, Any]], empty: str = "No data") -> None:
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.write(empty)


def _city_category(data: Any) -> None:
    rows = market.in_city(market.section(data, "by_city_category"), filters.city)
    st.metric("Companies", sum(int(r.get("companies") or 0) for r in rows))
    _table(market.city_category_matrix(rows))


def _ratings(data: Any) -> None:
    if isinstance(data, dict) and data.get("maps_available") is False:
        st.info(market.MAPS_UNAVAILABLE)
        return
    _table(market.in_city(market.section(data, "maps_by_city"), filters.city))
    listed = [r for r in market.section(data, "by_city_category") if r.get("listings")]
    by_category = market.in_city(listed, filters.city)
    if by_category:
        st.caption("By category")
        _table(by_category)


def _hiring(data: Any) -> None:
    _table(market.in_city(market.section(data, "hiring"), filters.city))


def _mid_size(data: Any) -> None:
    mid = data.get("mid_size") if isinstance(data, dict) else None
    mid = mid if isinstance(mid, dict) else {}
    rows = market.in_city(market.section(mid, "by_city"), filters.city)
    share = mid.get("share") if filters.city == "all" else (rows[0]["share"] if rows else None)
    cols = st.columns(2)
    cols[0].metric("Mid-size share", "No data" if share is None else f"{share:.0%}")
    cols[1].metric("Companies with known size", sum(int(r.get("with_size") or 0) for r in rows))
    st.caption(
        f"Mid-size: {mid.get('min_employees', 11)} to {mid.get('max_employees', 500)} employees"
    )
    _table(rows)


def _companies(data: Any) -> None:
    rows = market.in_city(market.company_rows(_common.rows_of(data)), filters.city)
    pending = sum(1 for r in rows if r["rues"] == market.RUES_LABELS["pending"])
    st.metric(market.RUES_LABELS["pending"], pending)
    _table(rows, "No companies")


ui.card("market-kpis", _common.api_url("pulse/pipeline-backlog", filters), _kpis, title="KPIs")
sources_url = _common.api_url("pulse/top-sources", filters)
ui.card("market-sources", sources_url, _sources, title="Top sources")
ui.card(
    "market-city-category", INSIGHTS_URL, _city_category, title="Companies by city and category"
)
ui.card("market-ratings", INSIGHTS_URL, _ratings, title="Maps rating and reviews")
ui.card("market-hiring", INSIGHTS_URL, _hiring, title="Hiring demand by city and keyword")
ui.card("market-mid-size", INSIGHTS_URL, _mid_size, title="Mid-size companies")
companies_url = _common.api_url("companies", filters, {"limit": "200"})
ui.card("market-companies", companies_url, _companies, title="Companies")
