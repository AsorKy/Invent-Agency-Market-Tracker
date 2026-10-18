"""Render the Segmentos page: segment sizes from the gateway.

The segments route belongs to a later layer, so the card shows its error with
a retry button until the endpoint exists. It relates to ``_common.py``.
"""

from typing import Any

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

filters = _common.start_page("Segmentos")


def _segments(data: Any) -> None:
    rows = _common.rows_of(data)
    if not rows:
        st.write("No segments")
        return
    st.dataframe(rows, use_container_width=True)
    sized = {str(r.get("segment", r.get("name", "?"))): float(r.get("size") or 0) for r in rows}
    st.bar_chart(sized)


ui.card("segments", _common.api_url("segments", filters), _segments, title="Segment sizes")
