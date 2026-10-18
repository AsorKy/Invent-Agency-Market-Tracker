"""Render the Ops page: freshness per source and the outbox backlog.

Only data exposed by the pulse ops endpoint is shown. Per-service latency and
Bright Data consumption have no endpoint yet and appear as a placeholder. It
relates to ``_common.py`` and ``app/ui.py``.
"""

from datetime import UTC, datetime
from typing import Any

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

OPS_ROLES = ("admin", "strategist", "analyst")

filters = _common.start_page("Ops", OPS_ROLES)


def _iso(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "unknown"
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def _ops(data: Any) -> None:
    if not isinstance(data, dict):
        st.write("No data")
        return
    backlog = data.get("outbox_backlog")
    st.metric("Outbox backlog", "unknown" if backlog is None else int(backlog))
    sources = _common.rows_of(data, "sources")
    if not sources:
        st.write("No sources")
        return
    st.dataframe(
        [
            {
                "source": row.get("source"),
                "last_load": _iso(row.get("as_of")),
                "status": "stale" if row.get("stale") else "ok",
            }
            for row in sources
        ],
        use_container_width=True,
    )
    if any(row.get("stale") for row in sources):
        st.warning("Some sources are stale")


ui.card("ops-health", _common.api_url("pulse/ops/health", filters), _ops, title="Service health")

with st.container(border=True):
    st.subheader("Latency and Bright Data usage")
    st.info("Per-service latency and Bright Data usage are not available yet.")
