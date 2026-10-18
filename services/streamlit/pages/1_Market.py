"""Render the Market radar page: KPIs and top sources from the pulse API.

Each card loads independently through ``ui.card``. It relates to
``_common.py`` and ``app/ui.py``.
"""

from typing import Any

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

filters = _common.start_page("Market")


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


ui.card("market-kpis", _common.api_url("pulse/pipeline-backlog", filters), _kpis, title="KPIs")
sources_url = _common.api_url("pulse/top-sources", filters)
ui.card("market-sources", sources_url, _sources, title="Top sources")
