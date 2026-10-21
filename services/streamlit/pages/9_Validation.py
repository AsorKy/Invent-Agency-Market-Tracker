"""Render the Validation page: cards V1 to V6 for one project.

Each card reads ``validation/projects/<id>/<card>`` through the gateway and
shows the latest values with threshold badges, ``measured_at`` and
``source_version``. A card whose fetch fails or returns nothing shows a
degraded notice while the others still render. No gateway endpoint serves
these reads yet, so every card degrades until it exists. It relates to
``_common.py``, ``app/ui.py`` and ``libs/domain/validation.py``.
"""

from functools import partial
from urllib.parse import quote

import streamlit as st

from services.streamlit.app import ui
from services.streamlit.pages import _common

VALIDATION_ROLES = ("admin", "strategist", "analyst")
CARDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "v1",
        "V1 Financial",
        "financial",
        ("npv", "irr", "payback_periods", "discounted_payback_periods", "emv"),
    ),
    (
        "v2",
        "V2 Unit economics",
        "unit-economics",
        ("cac", "ltv", "ltv_cac_ratio", "cac_payback_months", "gross_margin"),
    ),
    (
        "v3",
        "V3 Funnel",
        "funnel",
        ("conversion_rate", "win_rate", "cycle_time_days", "velocity", "coverage"),
    ),
    (
        "v4",
        "V4 Growth",
        "growth",
        ("mrr", "nrr", "grr", "rule_of_40", "burn_multiple", "magic_number"),
    ),
    ("v5", "V5 Portfolio", "portfolio", ("horizon_mix", "hhi", "sharpe_like")),
    (
        "v6",
        "V6 Meta-ML",
        "meta-ml",
        ("model_utility", "decision_latency_days", "psi", "precision_at_k", "funnel_lift"),
    ),
)

filters = _common.start_page("Validation", VALIDATION_ROLES)

projects = _common.rows_of(ui.cached_fetch(_common.api_url("projects", filters)).data)
ids = [str(row["id"]) for row in projects if row.get("id") is not None]
if not ids:
    st.warning("Degraded: no projects available")
    st.stop()
project_id = st.selectbox("Project", ids)

for key, title, path, fields in CARDS:
    url = _common.api_url(f"validation/projects/{quote(str(project_id), safe='')}/{path}", filters)
    ui.card(
        f"validation-{key}",
        url,
        partial(_common.render_validation, fields=fields),
        title=title,
    )
