"""dbt mart naming (decision D-19, REQ-PUL-003): marts start with mart_ and no
dbt model reuses the name of a table created by migrations."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "transform" / "models"
TIME_SPINE = "metricflow_time_spine"
CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?([\w.\"]+)", re.IGNORECASE
)


def _model_names(sub: str = "") -> set[str]:
    return {p.stem for p in (MODELS / sub).rglob("*.sql")}


def _migration_tables() -> set[str]:
    tables: set[str] = set()
    for path in (ROOT / "migrations").glob("*.sql"):
        for match in CREATE_RE.findall(path.read_text(encoding="utf-8")):
            tables.add(match.replace('"', "").split(".")[-1].lower())
    return tables


def test_marts_start_with_mart_prefix() -> None:
    names = _model_names("marts")
    assert names
    bad = sorted(n for n in names if n != TIME_SPINE and not n.startswith("mart_"))
    assert not bad, f"marts without mart_ prefix: {bad}"


def test_no_model_collides_with_migration_table() -> None:
    tables = _migration_tables()
    assert "fact_signal" in tables  # parser sanity check
    clash = sorted(_model_names() & tables)
    assert not clash, f"dbt models share names with migration tables: {clash}"
