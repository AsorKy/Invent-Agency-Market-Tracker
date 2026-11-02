"""Render the Leads page: scored lead inbox with decision makers and a role-gated export.

Each lead shows its company, city, stage, score, RUES label (``RUES pendiente`` for a company
without a RUES match, which is never hidden) and its decision makers (name, title and public
profile only, INV-12). When the tenant has no decision maker signal at all, a caption says so
instead of an empty column. It relates to ``_common.py``, ``app/ui.py`` and ``app/market.py``.
"""

import csv
import io
from typing import Any

import streamlit as st

from services.streamlit.app import market, ui
from services.streamlit.pages import _common

filters = _common.start_page("Leads")


def _leads(data: Any) -> None:
    raw = _common.rows_of(data)
    people = market.decision_makers_available(raw)
    rows = market.lead_rows(raw, with_people=people)
    if not rows:
        st.write("No leads")
        return
    pending = sum(1 for r in rows if r["rues"] == market.RUES_LABELS["pending"])
    cols = st.columns(2)
    cols[0].metric("Leads", len(rows))
    cols[1].metric(market.RUES_LABELS["pending"], pending)
    if not people:
        st.caption(market.DECISION_MAKERS_UNAVAILABLE)
    st.dataframe(rows, use_container_width=True)
    columns = list(rows[0])
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    payload = buffer.getvalue().encode()
    ui.export_button("Export CSV", payload, "leads.csv", "text/csv", key="leads-csv")


leads_url = _common.api_url("leads", filters, {"limit": "200"})
ui.card("leads-inbox", leads_url, _leads, title="Scored leads")
