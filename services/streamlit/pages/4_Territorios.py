"""Render the Territorios page: layer L1 territory data and an IOZ placeholder.

Only the L1 opportunity ranking is shown. The IOZ (layer L3) is an explicit
placeholder. It relates to ``_common.py``.
"""

from typing import Any

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

filters = _common.start_page("Territorios")


def _territories(data: Any) -> None:
    rows = _common.rows_of(data)
    if isinstance(data, dict) and data.get("method_version"):
        st.caption(f"Method version: {data['method_version']}")
    if not rows:
        st.write("No territories")
        return
    st.dataframe(rows, use_container_width=True)
    if any(r.get("low_sample") for r in rows):
        st.info("Some territories have a low sample size")


ui.card(
    "territory-opportunity",
    _common.api_url("territory/opportunity", filters),
    _territories,
    title="Opportunity ranking (L1)",
)

with st.container(border=True):
    st.subheader("IOZ")
    st.info("IOZ is not available yet. It arrives with layer L3.")
