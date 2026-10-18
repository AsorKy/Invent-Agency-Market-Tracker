"""Render the Leads page: scored lead inbox with a role-gated export.

It relates to ``_common.py`` and ``app/ui.py``.
"""

import csv
import io
from typing import Any

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

filters = _common.start_page("Leads")


def _leads(data: Any) -> None:
    rows = _common.rows_of(data)
    if not rows:
        st.write("No leads")
        return
    st.dataframe(rows, use_container_width=True)
    columns = sorted({key for row in rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    payload = buffer.getvalue().encode()
    ui.export_button("Export CSV", payload, "leads.csv", "text/csv", key="leads-csv")


ui.card("leads-inbox", _common.api_url("leads", filters), _leads, title="Scored leads")
