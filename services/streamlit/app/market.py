"""Shape the market and lead API bodies into display rows, without a Streamlit import.

The Market page (``pages/1_Market.py``) renders the sections of ``/api/v1/companies/insights``
and the company list; the Leads page (``pages/2_Leads.py``) renders the lead list. A company
that ingest delivered without a RUES match has ``rues_status`` ``pending`` and shows the label
``RUES pendiente``; it is never hidden. Every function accepts an empty or malformed body and
returns an empty list, so the views render without data. It relates to ``app/helpers.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RUES_LABELS = {
    "pending": "RUES pendiente",
    "matched": "RUES verificado",
    "unmatched": "RUES sin coincidencia",
    "quarantined": "RUES en cuarentena",
}
NO_VALUE = "-"
# Shown by the ratings card when the insights body says the tenant has no Maps signal.
MAPS_UNAVAILABLE = "Maps no disponible"
MAX_MATRIX_ROWS = 30


def rues_label(status: object) -> str:
    """Return the display label of a RUES status; unknown or missing reads as pending."""
    return RUES_LABELS.get(status if isinstance(status, str) else "pending", RUES_LABELS["pending"])


def section(data: Any, key: str) -> list[dict[str, Any]]:
    """Return the list of row dicts stored under ``key`` of a body, or an empty list."""
    rows = data.get(key) if isinstance(data, Mapping) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def in_city(rows: list[dict[str, Any]], city: str) -> list[dict[str, Any]]:
    """Keep the rows of the global city filter (``all`` keeps everything), ignoring case."""
    if city == "all":
        return rows
    wanted = city.casefold()
    return [row for row in rows if str(row.get("city") or "").casefold() == wanted]


def city_category_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pivot ``by_city_category`` rows to one row per category with one column per city."""
    cities = sorted({str(row.get("city") or NO_VALUE) for row in rows})
    matrix: dict[str, dict[str, Any]] = {}
    for row in rows:
        category = str(row.get("category") or NO_VALUE)
        line = matrix.setdefault(
            category, {"category": category, **dict.fromkeys(cities, 0), "total": 0}
        )
        count = int(row.get("companies") or 0)
        line[str(row.get("city") or NO_VALUE)] += count
        line["total"] += count
    ranked = sorted(matrix.values(), key=lambda line: (-line["total"], line["category"]))
    return ranked[:MAX_MATRIX_ROWS]


def company_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display rows of the company list with the RUES label."""
    return [
        {
            "company": row.get("legal_name"),
            "city": row.get("city"),
            "employees": row.get("employee_count"),
            "website": row.get("website"),
            "rues": rues_label(row.get("rues_status")),
        }
        for row in rows
    ]


def _people(value: Any) -> list[dict[str, Any]]:
    return [p for p in value if isinstance(p, dict)] if isinstance(value, list) else []


def lead_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display rows of the lead list: RUES label and decision makers as ``name (title)``."""
    shown = []
    for row in rows:
        people = _people(row.get("decision_makers"))
        shown.append(
            {
                "company": row.get("legal_name"),
                "city": row.get("city"),
                "stage": row.get("stage"),
                "score": row.get("score"),
                "rues": rues_label(row.get("rues_status")),
                "decision_makers": "; ".join(
                    f"{p.get('name') or NO_VALUE} ({p.get('title') or NO_VALUE})" for p in people
                )
                or NO_VALUE,
                "profiles": " ".join(str(p["profile_url"]) for p in people if p.get("profile_url")),
                "service_line": row.get("service_line"),
                "lead_id": row.get("id"),
            }
        )
    return shown
