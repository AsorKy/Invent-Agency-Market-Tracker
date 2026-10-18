"""Unit tests for the socioeconomic indices and the opportunity endpoint (T-059).

Covers REQ-SOC-001: file loading with path validation, z-scores within a city,
entropy weights, IPET, IMD, IOP, ``method_version``, ``low_sample``, and the
``/internal/pulse/territory/opportunity`` endpoint. No database is used.
"""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from libs.domain.socio import (
    INDICATOR_FIELDS,
    METHOD_VERSION,
    TerritoryRecord,
    compute_indices,
    entropy_weights,
    z_scores,
)

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")

from libs.common.settings import get_settings  # noqa: E402
from services.pulse.app import main as pulse  # noqa: E402
from services.pulse.app.models import TerritoryOpportunity  # noqa: E402
from services.pulse.app.repository import PulseRepository  # noqa: E402
from services.pulse.app.socio_loader import (  # noqa: E402
    SocioDataError,
    load_records,
    resolve_data_file,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "socio"
HEADERS = {"X-Service-Token": get_settings().internal_service_token}
HEADER_LINE = (
    "city,territory_code,territory_name,gdp_per_capita,formality_rate,"
    "youth_employment_rate,business_growth_rate,web_share,ecommerce_share,"
    "app_share,prospect_density\n"
)


def _record(city: str, code: str, **overrides: float) -> TerritoryRecord:
    base = {field: 10.0 for field in INDICATOR_FIELDS}
    base.update(overrides)
    return TerritoryRecord(city=city, territory_code=code, territory_name=code, indicators=base)


def test_z_scores_mean_zero_and_constant_series() -> None:
    zs = z_scores([1.0, 2.0, 3.0, 4.0])
    assert math.isclose(sum(zs), 0.0, abs_tol=1e-9)
    assert math.isclose(sum(z * z for z in zs) / 4, 1.0)
    assert z_scores([5.0, 5.0]) == [0.0, 0.0]
    assert z_scores([]) == []


def test_entropy_weights_sum_to_one_and_favor_dispersion() -> None:
    weights = entropy_weights([[1.0, 1.0, 1.0, 1.0], [0.0, 10.0, 0.0, 10.0]])
    assert math.isclose(sum(weights), 1.0)
    assert weights[1] > weights[0]
    assert entropy_weights([[1.0], [2.0]]) == [0.5, 0.5]
    assert entropy_weights([[3.0, 3.0], [4.0, 4.0]]) == [0.5, 0.5]


def test_z_scores_are_computed_within_each_city() -> None:
    records = [
        _record("A", "a1", gdp_per_capita=1.0),
        _record("A", "a2", gdp_per_capita=3.0),
        _record("B", "b1", gdp_per_capita=1000.0),
        _record("B", "b2", gdp_per_capita=3000.0),
    ]
    by_code = {t.territory_code: t for t in compute_indices(records)}
    assert by_code["a2"].z_scores["gdp_per_capita"] == pytest.approx(1.0)
    assert by_code["b2"].z_scores["gdp_per_capita"] == pytest.approx(1.0)


def test_iop_rewards_rich_digitally_poor_dense_territories() -> None:
    rich_gap = {
        "gdp_per_capita": 90.0,
        "formality_rate": 90.0,
        "web_share": 5.0,
        "ecommerce_share": 5.0,
        "app_share": 5.0,
        "prospect_density": 90.0,
    }
    rich_digital = {
        "gdp_per_capita": 90.0,
        "formality_rate": 90.0,
        "web_share": 90.0,
        "ecommerce_share": 90.0,
        "app_share": 90.0,
        "prospect_density": 90.0,
    }
    poor = {
        "gdp_per_capita": 5.0,
        "formality_rate": 5.0,
        "web_share": 5.0,
        "ecommerce_share": 5.0,
        "app_share": 5.0,
        "prospect_density": 5.0,
    }
    ranked = compute_indices(
        [
            _record("A", "gap", **rich_gap),
            _record("A", "dig", **rich_digital),
            _record("A", "poor", **poor),
        ]
    )
    assert [t.territory_code for t in ranked][0] == "gap"
    assert [t.rank for t in ranked] == [1, 2, 3]
    assert all(0.0 <= t.iop <= 1.0 and 0.0 <= t.ipet <= 1.0 for t in ranked)


def test_low_sample_below_five_territories_per_city() -> None:
    records = [_record("A", f"a{i}", gdp_per_capita=float(i)) for i in range(5)]
    records += [_record("B", f"b{i}", gdp_per_capita=float(i)) for i in range(4)]
    flags = {t.city: t.low_sample for t in compute_indices(records)}
    assert flags == {"A": False, "B": True}


def test_fixture_dataset_loads_and_ranks() -> None:
    records, as_of = load_records(FIXTURES)
    assert len(records) == 10
    assert as_of is not None
    ranked = compute_indices(records)
    assert len(ranked) == 10
    assert ranked[0].iop >= ranked[-1].iop
    assert {t.city for t in ranked} == {"Bogota", "Medellin", "Cali"}
    assert compute_indices(records) == ranked


def test_missing_directory_gives_no_records(tmp_path: Path) -> None:
    assert load_records(tmp_path / "absent") == ([], None)
    assert load_records(tmp_path) == ([], None)


@pytest.mark.parametrize(
    "name", ["../x.csv", "a/b.csv", "..", "x.txt", "", "/etc/passwd", "..\\x.csv", "a..b/c.csv"]
)
def test_path_traversal_is_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(SocioDataError):
        resolve_data_file(tmp_path, name)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text(HEADER_LINE)
    data = tmp_path / "data"
    data.mkdir()
    (data / "link.csv").symlink_to(outside)
    with pytest.raises(SocioDataError):
        load_records(data)


@pytest.mark.parametrize(
    "row",
    [
        "A,a1,A1,nan,70,60,5,50,30,10,5\n",
        "A,a1,A1,-1,70,60,5,50,30,10,5\n",
        "A,a1,A1,100,170,60,5,50,30,10,5\n",
        "A,a1,A1,abc,70,60,5,50,30,10,5\n",
        ",a1,A1,100,70,60,5,50,30,10,5\n",
    ],
)
def test_invalid_rows_are_rejected(tmp_path: Path, row: str) -> None:
    (tmp_path / "bad.csv").write_text(HEADER_LINE + row)
    with pytest.raises(SocioDataError):
        load_records(tmp_path)


def test_duplicate_and_missing_columns_are_rejected(tmp_path: Path) -> None:
    row = "A,a1,A1,100,70,60,5,50,30,10,5\n"
    (tmp_path / "dup.csv").write_text(HEADER_LINE + row + row)
    with pytest.raises(SocioDataError):
        load_records(tmp_path)
    (tmp_path / "dup.csv").write_text("city,territory_code\nA,a1\n")
    with pytest.raises(SocioDataError):
        load_records(tmp_path)


def test_model_forces_low_sample() -> None:
    row = TerritoryOpportunity(
        rank=1,
        city="A",
        territory_code="a",
        territory_name="a",
        ipet=0.5,
        imd=0.5,
        iop=0.5,
        n_in_city=3,
        low_sample=False,
    )
    assert row.low_sample is True


class FakeRepository:
    """Return canned territory rows or raise a data error."""

    def __init__(self, as_of: datetime | None, fail: bool = False) -> None:
        self.as_of = as_of
        self.fail = fail

    async def territory_opportunity(
        self, **_: Any
    ) -> tuple[list[TerritoryOpportunity], datetime | None, str]:
        if self.fail:
            raise SocioDataError("invalid_number")
        row = TerritoryOpportunity(
            rank=1,
            city="A",
            territory_code="a1",
            territory_name="A1",
            ipet=0.9,
            imd=0.1,
            iop=0.8,
            n_in_city=3,
        )
        return [row], self.as_of, METHOD_VERSION


def _client(repository: Any) -> Any:
    pulse.app.state.repository = repository
    return testclient.TestClient(pulse.app)


PATH = "/internal/pulse/territory/opportunity"


def test_endpoint_contract_fields_and_cache_header() -> None:
    client = _client(FakeRepository(datetime.now(UTC) - timedelta(hours=1)))
    response = client.get(PATH, headers=HEADERS)
    body = response.json()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=60"
    assert body["method_version"] == METHOD_VERSION
    assert body["stale"] is False
    assert isinstance(body["as_of"], int)
    assert body["territories"][0]["low_sample"] is True


def test_endpoint_stale_auth_and_data_error() -> None:
    old = datetime.now(UTC) - timedelta(hours=27)
    assert _client(FakeRepository(old)).get(PATH, headers=HEADERS).json()["stale"] is True
    assert _client(FakeRepository(old)).get(PATH).status_code == 401
    failing = _client(FakeRepository(old, fail=True)).get(PATH, headers=HEADERS)
    assert failing.status_code == 503
    assert failing.json()["detail"] == "socio_data_invalid"
    assert _client(FakeRepository(old)).get(PATH + "?limit=0", headers=HEADERS).status_code == 422


def test_real_repository_reads_fixture_directory() -> None:
    client = _client(PulseRepository(None, str(FIXTURES)))
    body = client.get(PATH + "?city=bogota&limit=3", headers=HEADERS).json()
    assert len(body["territories"]) == 3
    assert all(t["city"] == "Bogota" for t in body["territories"])
    assert [t["rank"] for t in body["territories"]] == [1, 2, 3]
    assert body["method_version"] == METHOD_VERSION
    cali = client.get(PATH + "?city=Cali", headers=HEADERS).json()["territories"]
    assert cali[0]["low_sample"] is True
