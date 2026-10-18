"""Render the Pipeline page: a kanban board with per-stage counts from the gateway.

It relates to ``_common.py`` and ``app/ui.py``.
"""

from typing import Any

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

filters = _common.start_page("Pipeline")


def _kanban(data: Any) -> None:
    columns = data.get("columns") if isinstance(data, dict) else None
    if not isinstance(columns, dict) or not columns:
        st.write("No pipeline data")
        return
    total = sum(len(v) for v in columns.values() if isinstance(v, list))
    st.metric("Leads in pipeline", total)
    cells = st.columns(len(columns))
    for cell, (stage, leads) in zip(cells, columns.items(), strict=True):
        rows = [row for row in leads if isinstance(row, dict)] if isinstance(leads, list) else []
        with cell:
            st.markdown(f"**{stage}**")
            st.metric("Leads", len(rows), label_visibility="collapsed")
            for row in rows:
                label = row.get("name") or row.get("company_name") or row.get("id") or "lead"
                st.caption(str(label))


ui.card(
    "pipeline-kanban",
    _common.api_url("pipeline/kanban", filters),
    _kanban,
    title="Commercial pipeline",
)
