"""Render the Proyectos page: formulated projects with status and ROI.

It relates to ``_common.py`` and ``app/ui.py``.
"""

import csv
import io
from typing import Any

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

filters = _common.start_page("Proyectos")


def _projects(data: Any) -> None:
    rows = _common.rows_of(data)
    if not rows:
        st.write("No projects")
        return
    st.dataframe(rows, use_container_width=True)
    columns = sorted({key for row in rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    ui.export_button(
        "Export CSV", buffer.getvalue().encode(), "projects.csv", "text/csv", key="projects-csv"
    )


ui.card("projects-list", _common.api_url("projects", filters), _projects, title="Projects")
