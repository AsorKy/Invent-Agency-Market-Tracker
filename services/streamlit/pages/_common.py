"""Share small helpers between the reporting pages.

The module applies the documented development role default and shared page
scaffolding. It is not a page (the name does not start with a digit), so
``Home.py`` does not register it. It relates to ``app/ui.py`` and
``app/helpers.py``.

Nothing in the app sets ``session_state["role"]`` yet. For local development
set ``STREAMLIT_DEV_ROLE`` (one of admin, strategist, intake, bd_rep, analyst);
without it, pages show the no-access message. Do not set it in production.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Any, cast

import streamlit as st

from libs.domain.validation import evaluate
from services.streamlit.app import helpers, ui

DEV_ROLE_ENV = "STREAMLIT_DEV_ROLE"


def apply_dev_role() -> None:
    """Seed ``session_state["role"]`` from ``STREAMLIT_DEV_ROLE`` when unset."""
    state = cast(MutableMapping[str, Any], st.session_state)
    role = os.environ.get(DEV_ROLE_ENV, "").strip()
    if helpers.current_role(state) is None and role in helpers.KNOWN_ROLES:
        state["role"] = role


def start_page(title: str, allowed: tuple[str, ...] | None = None) -> helpers.Filters:
    """Set the title, apply the role gate, and return the global filters."""
    apply_dev_role()
    st.title(title)
    ui.require_role(allowed)
    return ui.global_filters()


def api_url(path: str, filters: helpers.Filters, extra: dict[str, str] | None = None) -> str:
    """Build a gateway URL carrying the active city and service line filters."""
    params: dict[str, str] = dict(extra or {})
    if filters.city != "all":
        params["city"] = filters.city
    if filters.service_line != "all":
        params["service_line"] = filters.service_line
    url = helpers.gateway_url(f"/api/v1/{path.lstrip('/')}")
    if params:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode(params)}"
    return url


def rows_of(data: Any, key: str = "items") -> list[dict[str, Any]]:
    """Extract a list of row dicts from a list body or a ``{key: [...]}`` body."""
    if isinstance(data, dict):
        data = data.get(key, data.get("data", []))
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


# Metric name -> threshold key in libs/domain/validation_thresholds.yaml.
THRESHOLD_KEYS: dict[str, str] = {
    "irr": "irr",
    "payback_periods": "payback_months",
    "ltv_cac_ratio": "ltv_cac",
    "cac_payback_months": "cac_payback_months",
    "coverage": "pipeline_coverage",
    "burn_multiple": "burn_multiple",
    "nrr": "nrr",
    "grr": "grr",
    "rule_of_40": "rule_of_40",
    "magic_number": "magic_number",
    "psi": "psi",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def render_validation(data: Any, fields: tuple[str, ...]) -> None:
    """Render one validation card body, or a degraded notice when it has no data.

    ``data`` is the latest measurement row (a dict) or a list of rows, newest
    first. Each field shows its value and, when a threshold rule exists, a
    status badge. ``measured_at`` and ``source_version`` are always shown.
    """
    rows = rows_of(data) if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    row = rows[0] if rows else None
    if not row or not any(row.get(name) is not None for name in fields):
        st.warning("Degraded: no data for this card")
        return
    variant = row.get("horizon")
    for name in fields:
        value = row.get(name)
        number = _number(value)
        label = "n/a" if value is None else str(value)
        key = THRESHOLD_KEYS.get(name)
        if key is None:
            st.write(f"{name}: {label}")
            continue
        status = evaluate(key, number, variant=variant if isinstance(variant, str) else None)
        st.write(f"{name}: {label} [{status}]")
    st.caption(
        f"measured_at: {row.get('measured_at') or 'unknown'} | "
        f"source_version: {row.get('source_version') or 'unknown'}"
    )
