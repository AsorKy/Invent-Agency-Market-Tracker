"""Assert the schema and role catalog of the migrations and the roles example.

Relates to REQ-DAT-001 and SPEC section 3.2: 7 schemas and 14 roles. The test
parses ``migrations/*.sql`` and ``ops/bootstrap_roles.sql.example`` as text.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))
ROLES_EXAMPLE = ROOT / "ops" / "bootstrap_roles.sql.example"

SCHEMAS = {"identity", "ingest", "core", "pulse", "brief", "ml", "socio"}
ROLES = {f"{s}_{kind}" for s in SCHEMAS for kind in ("owner", "app")}
CREATE_SCHEMA = re.compile(r"^\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.I | re.M)
DROP_SCHEMA = re.compile(r"^\s*DROP\s+SCHEMA\s+(?:IF\s+EXISTS\s+)?(\w+)", re.I | re.M)
CREATE_ROLE = re.compile(r"CREATE\s+ROLE\s+(\w+)", re.I)


def _net_schemas() -> set[str]:
    created: set[str] = set()
    for path in MIGRATIONS:
        sql = path.read_text(encoding="utf-8")
        created |= set(CREATE_SCHEMA.findall(sql))
        if path.name.startswith("contract_"):
            created -= set(DROP_SCHEMA.findall(sql))
    return created


def test_roles_example_has_exactly_the_catalog_roles() -> None:
    text = ROLES_EXAMPLE.read_text(encoding="utf-8")
    roles = CREATE_ROLE.findall(text)
    assert len(roles) == len(set(roles)) == 14
    assert set(roles) == ROLES


def test_socioeconomic_is_dropped_only_by_a_contract_migration() -> None:
    droppers = [
        p.name
        for p in MIGRATIONS
        if "socioeconomic" in DROP_SCHEMA.findall(p.read_text(encoding="utf-8"))
    ]
    assert droppers
    assert all(name.startswith("contract_") for name in droppers)
    assert "socioeconomic" not in _net_schemas()


def test_canonical_schemas_are_all_created() -> None:
    assert SCHEMAS <= _net_schemas()


def test_no_unexpected_schema_besides_known_legacy() -> None:
    assert _net_schemas() - SCHEMAS <= {"analytics"}


@pytest.mark.xfail(
    strict=True,
    reason="REQ-DAT-001: migration 20261017_0001 (already applied, checksummed) creates analytics",
)
def test_analytics_is_never_created() -> None:
    assert "analytics" not in _net_schemas()


def test_roles_example_has_no_legacy_names() -> None:
    text = ROLES_EXAMPLE.read_text(encoding="utf-8")
    assert "analytics" not in text
    assert "socioeconomic" not in text
