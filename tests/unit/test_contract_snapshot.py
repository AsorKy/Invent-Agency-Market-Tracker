"""Freeze the shared contracts with JSON snapshots (T-014, REQ-EVT-005, REQ-EXT-001/004).

Stdlib-only snapshots (settings field names, event type values, schema and table catalog) are
computed by parsing source text, so they run without pydantic. The pydantic JSON-schema snapshot
of every model in ``libs/contracts`` needs pydantic and is skipped when pydantic is missing and
the snapshot file is absent. Regenerate every snapshot with
``UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py``; see docs/contracts-freeze.md.
"""

import ast
import importlib
import inspect
import json
import os
import pkgutil
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNAP = Path(__file__).resolve().parent / "snapshots"
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"

CREATE_SCHEMA = re.compile(r"^\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.I | re.M)
DROP_SCHEMA = re.compile(r"^\s*DROP\s+SCHEMA\s+(?:IF\s+EXISTS\s+)?(\w+)", re.I | re.M)
CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\.(\w+)\s*\((.*?)\n\);", re.I | re.S
)
ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\.(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+[\w()]+",
    re.I,
)
NOT_COLUMNS = {"unique", "primary", "check", "foreign", "constraint"}


def _class_node(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found in {path}")


def settings_fields() -> list[str]:
    node = _class_node(ROOT / "libs" / "common" / "settings.py", "Settings")
    return sorted(
        stmt.target.id
        for stmt in node.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    )


def _enum_values(name: str) -> dict[str, str]:
    node = _class_node(ROOT / "libs" / "contracts" / "events.py", name)
    values: dict[str, str] = {}
    for stmt in node.body:
        if (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Constant)
        ):
            values[stmt.targets[0].id] = str(stmt.value.value)
    return dict(sorted(values.items()))


def event_types() -> dict[str, dict[str, str]]:
    return {
        "EventType": _enum_values("EventType"),
        "ReservedEventType": _enum_values("ReservedEventType"),
    }


def table_catalog() -> dict[str, Any]:
    paths = sorted((ROOT / "migrations").glob("*.sql"))
    sql = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    schemas: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        schemas |= set(CREATE_SCHEMA.findall(text))
        if path.name.startswith("contract_"):
            schemas -= set(DROP_SCHEMA.findall(text))
    tables: dict[str, set[str]] = {}
    for schema, table, body in CREATE_TABLE.findall(sql):
        cols = tables.setdefault(f"{schema}.{table}", set())
        for line in body.splitlines():
            word = line.strip().split(" ")[0]
            if word and word.islower() and word not in NOT_COLUMNS:
                cols.add(word)
    for schema, table, col in ADD_COLUMN.findall(sql):
        tables.setdefault(f"{schema}.{table}", set()).add(col)
    return {
        "schemas": sorted(schemas),
        "tables": {name: sorted(cols) for name, cols in sorted(tables.items())},
    }


def _check(name: str, actual: Any) -> None:
    path = SNAP / name
    rendered = json.dumps(actual, indent=2, sort_keys=True) + "\n"
    if UPDATE:
        SNAP.mkdir(exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    assert path.exists(), f"{name} missing; run UPDATE_SNAPSHOTS=1 pytest {Path(__file__).name}"
    assert json.loads(path.read_text(encoding="utf-8")) == actual, (
        f"{name} drifted from the frozen contract. Contract edits need the coordinator; "
        "see docs/contracts-freeze.md"
    )


def test_settings_field_names_frozen() -> None:
    _check("settings_fields.json", settings_fields())


def test_event_type_values_frozen() -> None:
    _check("event_types.json", event_types())


def test_event_type_values_are_unique_and_disjoint() -> None:
    data = event_types()
    live = set(data["EventType"].values())
    reserved = set(data["ReservedEventType"].values())
    assert len(live) == len(data["EventType"])
    assert len(reserved) == len(data["ReservedEventType"])
    assert not live & reserved


def test_table_catalog_frozen() -> None:
    _check("table_catalog.json", table_catalog())


def _model_schemas() -> dict[str, Any]:
    from pydantic import BaseModel

    pkg = importlib.import_module("libs.contracts")
    out: dict[str, Any] = {}
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"libs.contracts.{info.name}")
        for name, obj in sorted(vars(module).items()):
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj.__module__ == module.__name__
            ):
                out[f"{info.name}.{name}"] = obj.model_json_schema()
    enums = importlib.import_module("libs.contracts.events")
    out["events.EventType.values"] = sorted(m.value for m in enums.EventType)
    out["events.ReservedEventType.values"] = sorted(m.value for m in enums.ReservedEventType)
    return out


def test_pydantic_model_schemas_frozen() -> None:
    path = SNAP / "model_schemas.json"
    if not UPDATE and not path.exists():
        try:
            import pydantic  # noqa: F401
        except ImportError:
            pytest.skip("pydantic missing and model_schemas.json not generated yet")
        pytest.fail(
            "model_schemas.json is absent: the coordinator must generate it in CI with "
            "UPDATE_SNAPSHOTS=1 pytest tests/unit/test_contract_snapshot.py and commit it"
        )
    pytest.importorskip("pydantic")
    _check("model_schemas.json", _model_schemas())
