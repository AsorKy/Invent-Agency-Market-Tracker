"""Render Streamlit cards, role gates, filters, and exports.

This module is the thin Streamlit layer over ``services/streamlit/app/helpers.py``
and is used by ``Home.py`` and the pages. Each card is isolated: a failed query
shows an error with its own retry button and leaves other cards untouched.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from typing import Any, cast

import streamlit as st

from services.streamlit.app import helpers

CACHE_TTL_SECONDS = 60


def _state() -> MutableMapping[str, Any]:
    """View session state as a plain mutable mapping."""
    return cast(MutableMapping[str, Any], st.session_state)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_fetch(url: str) -> helpers.Fetched:
    """Cache one card query for 60 seconds, keyed by URL."""
    return helpers.safe_fetch(url)


def require_role(allowed: Sequence[str] | None = None) -> None:
    """Stop the page with an info message when the session role lacks access."""
    if not helpers.has_access(_state(), allowed):
        st.info(helpers.NO_ACCESS)
        st.stop()


def card(
    key: str,
    url: str,
    render: Callable[[Any], None],
    *,
    title: str | None = None,
) -> None:
    """Render one card: freshness caption, stale warning, or error with retry."""
    with st.container(border=True):
        if title:
            st.subheader(title)
        placeholder = st.empty()
        with st.spinner("Loading"):
            result = cached_fetch(url)
        with placeholder.container():
            if result.error is not None:
                st.error(result.error)
                if st.button("Retry", key=f"retry-{key}"):
                    cached_fetch.clear(url)
                    st.rerun()
                return
            render(result.data)
            st.caption(helpers.format_as_of(result.as_of))
            if result.stale:
                st.warning("Stale data")


def export_button(label: str, data: bytes, file_name: str, mime: str, *, key: str) -> None:
    """Show a download button only for roles allowed to export."""
    if helpers.can_export(_state()):
        st.download_button(label, data, file_name=file_name, mime=mime, key=key)


def global_filters() -> helpers.Filters:
    """Sync global filters between session state and ``st.query_params``."""
    return helpers.sync_filters(_state(), cast(MutableMapping[str, str], st.query_params))
