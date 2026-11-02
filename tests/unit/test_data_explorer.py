"""Unit tests for the read-only data explorer of the five owning services (INV-01, INV-12).

Checks the dataset definitions of ``services/<service>/app/data_explorer.py`` against the
schema that ``migrations/`` build (every column except the hidden hashes, the keyset key, the
fixed binds), the cursor and cell rules of ``libs/common/data_explorer.py``, and the internal
routes with a fake engine: gateway token only, tenant context first, the tenant bound, ingest
rows only for ``INGEST_TENANT_ID``, and ml rows only for the companies core lists for the
tenant. No network, Redis, or Postgres is used.
"""

import base64
import importlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pglast")
import pglast  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from support_sql import load_schema, replace_binds  # noqa: E402

from libs.common.data_explorer import (  # noqa: E402
    CATALOG,
    CELL_LIMIT,
    DATA_SERVICES,
    TRUNCATION_MARKER,
    Dataset,
    data_router,
    decode_page_cursor,
    encode_page_cursor,
    render_cell,
)
from services.brief.app import data_explorer as brief_data  # noqa: E402
from services.core.app import data_explorer as core_data  # noqa: E402
from services.ingest.app import data_explorer as ingest_data  # noqa: E402
from services.ingest.app.tenant import IngestTenantSettings, load_ingest_tenant_id  # noqa: E402
from services.ml.app import data_explorer as ml_data  # noqa: E402
from services.ml.app.core_features import (  # noqa: E402
    CompanyFeatures,
    CoreFeaturesPage,
    CoreFeaturesUnavailable,
)
from services.pulse.app import data_explorer as pulse_data  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402
from tests.unit.support_cov import FakeEngine, FakeResult  # noqa: E402

SERVICE_DATASETS: dict[str, tuple[Dataset, ...]] = {
    "ingest": ingest_data.DATASETS,
    "core": core_data.DATASETS,
    "ml": ml_data.DATASETS,
    "brief": brief_data.DATASETS,
    "pulse": pulse_data.DATASETS,
}
ALL = [dataset for datasets in SERVICE_DATASETS.values() for dataset in datasets]
BY_ID = {dataset.id: dataset for dataset in ALL}
MAINS = {service: f"services.{service}.app.main" for service in SERVICE_DATASETS}
# Bind values the scope of each service adds to every statement.
SCOPE_BINDS = {
    "ingest": set(),
    "core": {"tenant_id"},
    "ml": {"company_ids"},
    "brief": {"tenant_id"},
    "pulse": {"tenant_id"},
}
# Table columns that are never returned, with the reason.
HIDDEN = {
    "ingest.raw_snapshot": {"payload_sha256"},  # payload hash
    "ingest.pending_snapshot": {"payload_sha256"},  # payload hash
    "ingest.entity_candidate": {"candidate_key"},  # sha256 of the candidate identity
}
SECRET_LOOKING = re.compile(r"token|secret|passw|credential|hash|sha\d*|api_?key|_key$", re.I)
BIND = re.compile(r"(?<![:\w]):(\w+)")
TENANT = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _value(kind: str, index: int = 0) -> Any:
    return {
        "uuid": uuid4(),
        "text": f"value {index}",
        "integer": index,
        "bigint": 100 - index,
        "numeric": Decimal("0.125"),
        "boolean": False,
        "timestamptz": NOW - timedelta(minutes=index),
        "date": date(2026, 10, 30) - timedelta(days=index),
        "jsonb": json.dumps({"index": index, "name": "Caf\u00e9"}),
    }[kind]


def _row(dataset: Dataset, index: int = 0, **values: Any) -> dict[str, Any]:
    return {name: values.get(name, _value(kind, index)) for name, kind in dataset.columns}


def _parse(sql: str) -> Any:
    return pglast.parse_sql(replace_binds(sql))[0].stmt


def _table(dataset: Dataset) -> str:
    """The ``schema.table`` the count statement reads."""
    relation = _parse(dataset.count.text).fromClause[0]
    return f"{relation.schemaname}.{relation.relname}"


def _select_names(sql: str) -> list[str]:
    return [target.name or target.val.fields[-1].sval for target in _parse(sql).targetList]


def _client(monkeypatch: pytest.MonkeyPatch, service: str, engine: Any, **state: Any) -> Any:
    module = importlib.import_module(MAINS[service])
    monkeypatch.setattr(module.app.state, "data_engine", engine, raising=False)
    for name, value in state.items():
        monkeypatch.setattr(module.app.state, name, value, raising=False)
    return TestClient(module.app)


def _headers(tenant: UUID | None = TENANT, service: str = "gateway") -> dict[str, str]:
    extra = {"X-Tenant-Id": str(tenant)} if tenant else {}
    return service_headers(service, **extra)


def _serve(engine: FakeEngine, total: int, rows: list[dict[str, Any]]) -> None:
    engine.connection.route("count(*)", FakeResult(scalar=total))
    engine.connection.route("ORDER BY", FakeResult(rows=rows))


# --------------------------------------------------------------------------- #
# Dataset definitions
# --------------------------------------------------------------------------- #


def test_every_catalog_dataset_is_served_once_by_its_owner() -> None:
    served = {dataset.id: service for service, ds in SERVICE_DATASETS.items() for dataset in ds}
    assert len(served) == len(ALL)
    assert served == {dataset_id: info.service for dataset_id, info in CATALOG.items()}
    assert set(DATA_SERVICES) == set(SERVICE_DATASETS)
    assert {info.origin for info in CATALOG.values() if info.service != "ingest"} == {"derived"}
    assert CATALOG["raw_snapshots"].origin == "bright_data_raw"


def test_columns_are_the_whole_table_except_the_hidden_hashes() -> None:
    schema = load_schema()
    for dataset in ALL:
        names = [name for name, _ in dataset.columns]
        table = set(schema.tables[_table(dataset)].columns)
        assert len(names) == len(set(names)), dataset.id
        assert set(names) | set(dataset.hidden) == table, dataset.id
        assert not set(names) & set(dataset.hidden), dataset.id
        assert set(dataset.key) <= set(names), dataset.id
    assert {_table(d): set(d.hidden) for d in ALL if d.hidden} == HIDDEN


def test_no_secret_looking_column_is_returned() -> None:
    returned = [
        f"{d.id}.{name}" for d in ALL for name, _ in d.columns if SECRET_LOOKING.search(name)
    ]
    assert returned == []
    assert all(SECRET_LOOKING.search(name) for names in HIDDEN.values() for name in names)


@pytest.mark.parametrize("dataset", ALL, ids=lambda d: d.id)
def test_page_statement_selects_the_columns_and_binds_only_fixed_values(dataset: Dataset) -> None:
    page, count = dataset.page.text, dataset.count.text
    assert f"FROM {_table(dataset)}\n" in page
    assert _select_names(page) == [name for name, _ in dataset.columns]
    for name, kind in dataset.columns:
        if kind == "jsonb":
            assert f"left({name}::text, {CELL_LIMIT + 1}) AS {name}" in page
    scope = SCOPE_BINDS[dataset.info.service]
    keys = {f"k{index}" for index in range(1, len(dataset.key) + 1)}
    assert set(BIND.findall(page)) == {"q", "limit", *keys, *scope}
    assert set(BIND.findall(count)) == {"q", *scope}
    kinds = dict(dataset.columns)
    for index, name in enumerate(dataset.key, start=1):
        assert f"CAST(:k{index} AS {kinds[name]})" in page
    assert "ORDER BY " + ", ".join(f"{name} DESC" for name in dataset.key) in page
    if "tenant_id" in scope:
        assert "WHERE tenant_id = :tenant_id" in page and "WHERE tenant_id = :tenant_id" in count


def test_a_router_refuses_a_dataset_of_another_service() -> None:
    with pytest.raises(ValueError, match="does not own"):
        data_router("core", ingest_data.DATASETS)


# --------------------------------------------------------------------------- #
# Cursor and cells
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dataset", ALL, ids=lambda d: d.id)
def test_cursor_round_trips_the_key_of_the_last_row(dataset: Dataset) -> None:
    row = _row(dataset, 3)
    binds = decode_page_cursor(dataset, encode_page_cursor(dataset, row))
    assert binds == {f"k{i}": row[name] for i, name in enumerate(dataset.key, start=1)}


def _raw_cursor(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not a cursor!",
        _raw_cursor(["x"]),
        _raw_cursor({"dataset": "leads", "key": [NOW.isoformat(), str(uuid4())]}),
        _raw_cursor({"dataset": "companies", "key": [NOW.isoformat()]}),
        _raw_cursor({"dataset": "companies", "key": ["2026-10-30T12:00:00", str(uuid4())]}),
        _raw_cursor({"dataset": "companies", "key": [NOW.isoformat(), "not-a-uuid"]}),
        _raw_cursor({"dataset": "companies", "key": [1, str(uuid4())]}),
    ],
)
def test_invalid_cursors_are_refused(cursor: str) -> None:
    with pytest.raises(ValueError, match="invalid_cursor"):
        decode_page_cursor(BY_ID["companies"], cursor)


def test_a_boolean_is_not_a_bigint_key() -> None:
    cursor = _raw_cursor({"dataset": "rues_queue", "key": [NOW.isoformat(), True]})
    with pytest.raises(ValueError, match="invalid_cursor"):
        decode_page_cursor(BY_ID["rues_queue"], cursor)


def test_cells_keep_json_and_become_plain_values() -> None:
    assert render_cell('{"a": [1, 2]}', "jsonb") == ({"a": [1, 2]}, False)
    assert render_cell({"already": "decoded"}, "jsonb") == ({"already": "decoded"}, False)
    assert render_cell(b'["x"]', "jsonb") == (["x"], False)
    uid = uuid4()
    assert render_cell(uid, "uuid") == (str(uid), False)
    assert render_cell(NOW, "timestamptz") == (NOW.isoformat(), False)
    assert render_cell(date(2026, 1, 2), "date") == ("2026-01-02", False)
    assert render_cell(Decimal("0.5"), "numeric") == (0.5, False)
    assert render_cell(Decimal("NaN"), "numeric") == ("NaN", False)
    assert render_cell(None, "jsonb") == (None, False)
    assert render_cell(7, "bigint") == (7, False)


def test_cells_longer_than_the_limit_are_cut_with_a_marker() -> None:
    long_json = json.dumps({"payload": "x" * CELL_LIMIT})
    cell, cut = render_cell(long_json[: CELL_LIMIT + 1], "jsonb")
    assert cut and cell == long_json[:CELL_LIMIT] + TRUNCATION_MARKER
    exact = "y" * CELL_LIMIT
    assert render_cell(exact, "text") == (exact, False)
    cell, cut = render_cell(exact + "z", "text")
    assert cut and cell == exact + TRUNCATION_MARKER


# --------------------------------------------------------------------------- #
# Internal routes
# --------------------------------------------------------------------------- #


def test_core_page_sets_the_tenant_first_and_binds_every_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = BY_ID["companies"]
    rows = [_row(dataset, i, source_flags='{"rues_status": "pending"}') for i in range(3)]
    engine = FakeEngine()
    _serve(engine, 7, rows)
    client = _client(monkeypatch, "core", engine)

    response = client.get(
        "/internal/core/data/companies", params={"limit": 2, "q": "  Acme "}, headers=_headers()
    )

    assert response.status_code == 200
    body = response.json()
    (context_sql, context), (count_sql, count), (page_sql, page) = engine.connection.calls
    assert "set_config('app.tenant_id'" in context_sql and context == {"tenant_id": str(TENANT)}
    assert "count(*)" in count_sql and count == {"tenant_id": TENANT, "q": "Acme"}
    assert page == {"tenant_id": TENANT, "q": "Acme", "k1": None, "k2": None, "limit": 3}
    assert engine.log == ["enter:begin", "exit:begin:ok"]
    assert body["dataset"] == "companies" and body["total"] == 7
    assert body["columns"] == dataset.column_list()
    assert [row["id"] for row in body["data"]] == [str(r["id"]) for r in rows[:2]]
    assert body["data"][0]["source_flags"] == {"rues_status": "pending"}
    assert body["truncated_cells"] is False and body["next_cursor"]

    engine.connection.calls.clear()
    _serve(engine, 7, rows[2:])
    response = client.get(
        "/internal/core/data/companies",
        params={"limit": 2, "cursor": body["next_cursor"]},
        headers=_headers(),
    )

    page = engine.connection.calls[2][1]
    assert page["k1"] == rows[1]["updated_at"] and page["k2"] == rows[1]["id"]
    assert response.json()["next_cursor"] is None


def test_a_long_cell_is_truncated_and_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = BY_ID["signals"]
    payload = json.dumps({"text": "a" * CELL_LIMIT})[: CELL_LIMIT + 1]
    engine = FakeEngine()
    _serve(engine, 1, [_row(dataset, payload=payload)])

    body = (
        _client(monkeypatch, "core", engine)
        .get("/internal/core/data/signals", headers=_headers())
        .json()
    )

    assert body["truncated_cells"] is True
    assert body["data"][0]["payload"].endswith(TRUNCATION_MARKER)
    assert len(body["data"][0]["payload"]) == CELL_LIMIT + len(TRUNCATION_MARKER)


def test_core_catalog_lists_its_datasets_with_tenant_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    engine.connection.route("count(*)", FakeResult(scalar=3), FakeResult(scalar=0))
    engine.connection.route("count(*)", FakeResult(scalar=9))

    body = (
        _client(monkeypatch, "core", engine)
        .get("/internal/core/data/catalog", headers=_headers())
        .json()
    )

    assert [entry["id"] for entry in body["datasets"]] == ["companies", "signals", "leads"]
    assert [entry["count"] for entry in body["datasets"]] == [3, 0, 9]
    first = body["datasets"][0]
    assert first == {
        "id": "companies",
        "title": CATALOG["companies"].title,
        "description": CATALOG["companies"].description,
        "service": "core",
        "origin": "derived",
        "count": 3,
        "columns": BY_ID["companies"].column_list(),
    }
    assert all(
        params == {"tenant_id": TENANT, "q": None} for _, params in engine.connection.calls[1:]
    )


def test_catalog_without_tenant_or_database_has_null_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    client = _client(monkeypatch, "pulse", engine)
    body = client.get("/internal/pulse/data/catalog", headers=_headers(None)).json()
    assert [entry["count"] for entry in body["datasets"]] == [None, None]
    assert engine.connection.calls == []

    class Down:
        def begin(self) -> Any:
            raise OSError("database down")

    client = _client(monkeypatch, "pulse", Down())
    body = client.get("/internal/pulse/data/catalog", headers=_headers()).json()
    assert [entry["count"] for entry in body["datasets"]] == [None, None]


@pytest.mark.parametrize(
    ("headers", "path", "params", "status", "detail"),
    [
        (lambda: _headers(service="seed"), "companies", {}, 401, "invalid_service_token"),
        (lambda: {}, "companies", {}, 401, "invalid_service_token"),
        (lambda: _headers(None), "companies", {}, 400, "tenant_required"),
        (lambda: _headers(), "raw_snapshots", {}, 404, "dataset_not_found"),
        (lambda: _headers(), "companies", {"cursor": "garbage!"}, 400, "invalid_cursor"),
        (lambda: _headers(), "companies", {"limit": 0}, 422, None),
        (lambda: _headers(), "companies", {"limit": 201}, 422, None),
        (lambda: _headers(), "companies", {"q": "x" * 201}, 422, None),
    ],
)
def test_page_refusals(
    monkeypatch: pytest.MonkeyPatch,
    headers: Any,
    path: str,
    params: dict[str, Any],
    status: int,
    detail: str | None,
) -> None:
    engine = FakeEngine()
    client = _client(monkeypatch, "core", engine)

    response = client.get(f"/internal/core/data/{path}", params=params, headers=headers())

    assert response.status_code == status
    if detail:
        assert response.json() == {"detail": detail}
    assert engine.connection.calls == []


def test_catalog_needs_the_gateway_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "brief", FakeEngine())
    response = client.get("/internal/brief/data/catalog", headers=_headers(service="ml"))
    assert response.status_code == 401


def test_page_without_database_or_with_a_broken_one_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "brief", None)
    response = client.get("/internal/brief/data/record_digests", headers=_headers())
    assert response.status_code == 503 and response.json() == {"detail": "database_unavailable"}

    class Down:
        def begin(self) -> Any:
            raise OSError("database down")

    client = _client(monkeypatch, "brief", Down())
    response = client.get("/internal/brief/data/record_digests", headers=_headers())
    assert response.status_code == 503 and response.json() == {"detail": "database_unavailable"}


@pytest.mark.parametrize(
    ("service", "dataset_id"), [("brief", "record_digests"), ("pulse", "daily_rollups")]
)
def test_three_column_keys_page_on(
    monkeypatch: pytest.MonkeyPatch, service: str, dataset_id: str
) -> None:
    dataset = BY_ID[dataset_id]
    rows = [_row(dataset, i) for i in range(2)]
    engine = FakeEngine()
    _serve(engine, 2, rows)
    client = _client(monkeypatch, service, engine)
    path = f"/internal/{service}/data/{dataset_id}"

    body = client.get(path, params={"limit": 1}, headers=_headers()).json()
    engine.connection.calls.clear()
    _serve(engine, 2, rows[1:])
    client.get(path, params={"limit": 1, "cursor": body["next_cursor"]}, headers=_headers())

    page = engine.connection.calls[2][1]
    assert page["tenant_id"] == TENANT
    assert [page[f"k{i}"] for i in (1, 2, 3)] == [rows[0][name] for name in dataset.key]


def test_ingest_rows_are_shown_to_the_ingest_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = load_ingest_tenant_id()
    dataset = BY_ID["raw_snapshots"]
    engine = FakeEngine()
    _serve(engine, 1, [_row(dataset)])

    body = (
        _client(monkeypatch, "ingest", engine)
        .get("/internal/ingest/data/raw_snapshots", headers=_headers(owner))
        .json()
    )

    assert body["total"] == 1 and body["data"][0]["payload"] == {"index": 0, "name": "Caf\u00e9"}
    assert "payload_sha256" not in body["data"][0]
    assert engine.connection.calls[0][1] == {"tenant_id": str(owner)}
    assert engine.connection.calls[2][1] == {"q": None, "k1": None, "k2": None, "limit": 51}


def test_ingest_rows_are_hidden_from_any_other_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    other = uuid4()
    assert other != load_ingest_tenant_id()
    engine = FakeEngine()
    client = _client(monkeypatch, "ingest", engine)

    page = client.get("/internal/ingest/data/failed_records", headers=_headers(other)).json()
    catalog = client.get("/internal/ingest/data/catalog", headers=_headers(other)).json()

    assert page["data"] == [] and page["total"] == 0 and page["next_cursor"] is None
    assert [entry["count"] for entry in catalog["datasets"]] == [0] * 6
    assert engine.connection.calls == []


def test_ingest_without_its_tenant_setting_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_TENANT_ID", raising=False)
    monkeypatch.setattr(
        ingest_data,
        "load_ingest_tenant_id",
        lambda: IngestTenantSettings(_env_file=None).ingest_tenant_id,  # type: ignore[call-arg]
    )
    client = _client(monkeypatch, "ingest", FakeEngine())

    response = client.get("/internal/ingest/data/raw_snapshots", headers=_headers())
    catalog = client.get("/internal/ingest/data/catalog", headers=_headers()).json()

    assert response.status_code == 503 and response.json() == {"detail": "scope_unavailable"}
    assert {entry["count"] for entry in catalog["datasets"]} == {None}


class FakeCoreFeatures:
    """Core's tenant company list, in pages, or unavailable."""

    def __init__(self, pages: list[list[UUID]], *, down: bool = False) -> None:
        self.pages = pages
        self.down = down
        self.calls: list[tuple[UUID, int, UUID | None]] = []

    async def get_page(self, tenant_id: UUID, limit: int, after: UUID | None) -> CoreFeaturesPage:
        self.calls.append((tenant_id, limit, after))
        if self.down:
            raise CoreFeaturesUnavailable("core answered 503")
        ids = self.pages[len(self.calls) - 1]
        last = len(self.calls) == len(self.pages)
        companies = [
            CompanyFeatures(
                company_id=company_id,
                icp_fit=0.5,
                signal_strength=0.5,
                territory_opportunity=0.5,
                source_version="v1",
            )
            for company_id in ids
        ]
        return CoreFeaturesPage(companies=companies, next_after=None if last else ids[-1])


def test_ml_scores_are_limited_to_the_companies_core_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = [uuid4(), uuid4()], [uuid4()]
    features = FakeCoreFeatures([first, second])
    dataset = BY_ID["lead_scores"]
    engine = FakeEngine()
    _serve(engine, 1, [_row(dataset, company_id=first[0])])

    body = (
        _client(monkeypatch, "ml", engine, core_features=features)
        .get("/internal/ml/data/lead_scores", params={"q": "rule"}, headers=_headers())
        .json()
    )

    assert features.calls == [
        (TENANT, ml_data.CORE_PAGE_SIZE, None),
        (TENANT, ml_data.CORE_PAGE_SIZE, first[-1]),
    ]
    count, page = engine.connection.calls[1][1], engine.connection.calls[2][1]
    assert count == {"company_ids": first + second, "q": "rule"}
    assert page["company_ids"] == first + second
    assert body["data"][0]["company_id"] == str(first[0])
    assert body["data"][0]["feature_icp_fit"] == 0.125


def test_ml_without_core_answers_503_and_a_null_count(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    client = _client(monkeypatch, "ml", engine, core_features=FakeCoreFeatures([], down=True))

    response = client.get("/internal/ml/data/lead_scores", headers=_headers())
    catalog = client.get("/internal/ml/data/catalog", headers=_headers()).json()

    assert response.status_code == 503 and response.json() == {"detail": "scope_unavailable"}
    assert catalog["datasets"][0]["count"] is None
    assert engine.connection.calls == []

    client = _client(monkeypatch, "ml", engine, core_features=None)
    assert client.get("/internal/ml/data/lead_scores", headers=_headers()).status_code == 503
