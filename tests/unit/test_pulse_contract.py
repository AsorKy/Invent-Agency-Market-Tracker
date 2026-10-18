"""Unit tests for the Pulse endpoints and response rules (T-051).

Covers REQ-PUL-001 (five endpoints, ``as_of``, 26-hour ``stale``, ``low_sample``,
null for no data, ``Cache-Control``) and REQ-EXT-004 (optional ``method_version``).
The repository is replaced by an in-memory fake, so no database is used. The
module skips when runtime dependencies are absent.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")

from libs.common.settings import get_settings  # noqa: E402
from libs.contracts.pulse import STALE_AFTER_MS, is_low_sample, is_stale  # noqa: E402
from services.pulse.app import main as pulse  # noqa: E402
from services.pulse.app.models import (  # noqa: E402
    PipelineBacklog,
    PipelineTimeGroup,
    SignalPoint,
    SourceEfficiency,
    SourceFreshness,
    TopSource,
)

HEADERS = {"X-Service-Token": get_settings().internal_service_token}
CACHE = "private, max-age=60"
ENDPOINTS = [
    "/internal/pulse/unprocessed-signals",
    "/internal/pulse/pipeline-backlog",
    "/internal/pulse/source-efficiency",
    "/internal/pulse/time-in-pipeline",
    "/internal/pulse/top-sources",
    "/internal/pulse/ops/health",
]


class FakeRepository:
    """Return canned rows, or empty projections when ``empty`` is set."""

    def __init__(self, as_of: datetime | None, empty: bool = False) -> None:
        self.as_of = as_of
        self.empty = empty

    async def unprocessed_signals(self, **_: Any) -> tuple[list[SignalPoint], datetime | None]:
        rows = [] if self.empty else [SignalPoint(t="2026-10-12", total=10, failed=2, rate=0.2)]
        return rows, self.as_of

    async def pipeline_backlog(self, **_: Any) -> tuple[PipelineBacklog | None, datetime | None]:
        metrics = None if self.empty else PipelineBacklog(active=41, hot=7, capacity_estimate=50)
        return metrics, self.as_of

    async def source_efficiency(self, **_: Any) -> tuple[list[SourceEfficiency], datetime | None]:
        rows = [] if self.empty else [SourceEfficiency(name="rues", leads=3, meetings=1)]
        return rows, self.as_of

    async def time_in_pipeline(self, **_: Any) -> tuple[list[PipelineTimeGroup], datetime | None]:
        rows = (
            []
            if self.empty
            else [
                PipelineTimeGroup(key="saas", p50_days=3.0, p90_days=9.0, n=4),
                PipelineTimeGroup(key="data", p50_days=2.0, p90_days=5.0, n=5),
            ]
        )
        return rows, self.as_of

    async def top_sources(self, **_: Any) -> tuple[list[TopSource], datetime | None]:
        rows = [] if self.empty else [TopSource(source="rues", qualified_leads=4)]
        return rows, self.as_of

    async def ops_health(self) -> tuple[list[SourceFreshness], int | None, datetime | None]:
        if self.empty:
            return [], None, None
        old = int((datetime.now(UTC) - timedelta(hours=30)).timestamp() * 1000)
        return [SourceFreshness(source="rues", as_of=old)], 3, self.as_of


def _client(repository: FakeRepository) -> Any:
    pulse.app.state.repository = repository
    return testclient.TestClient(pulse.app)


def test_rule_helpers() -> None:
    assert STALE_AFTER_MS == 93_600_000
    assert not is_stale(0, STALE_AFTER_MS)
    assert is_stale(0, STALE_AFTER_MS + 1)
    assert is_low_sample(4)
    assert not is_low_sample(5)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_fresh_response_has_as_of_cache_header_and_method_version(path: str) -> None:
    response = _client(FakeRepository(datetime.now(UTC) - timedelta(hours=1))).get(
        path, headers=HEADERS
    )
    body = response.json()
    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE
    assert isinstance(body["as_of"], int)
    assert body["stale"] is False
    assert body["method_version"] is None


@pytest.mark.parametrize("path", ENDPOINTS)
def test_stale_after_26_hours(path: str) -> None:
    old = datetime.now(UTC) - timedelta(hours=27)
    body = _client(FakeRepository(old)).get(path, headers=HEADERS).json()
    assert body["stale"] is True
    recent = datetime.now(UTC) - timedelta(hours=25)
    assert _client(FakeRepository(recent)).get(path, headers=HEADERS).json()["stale"] is False


@pytest.mark.parametrize("path", ENDPOINTS)
def test_requires_internal_token(path: str) -> None:
    assert _client(FakeRepository(None)).get(path).status_code == 401


def test_low_sample_is_set_below_five() -> None:
    body = (
        _client(FakeRepository(datetime.now(UTC)))
        .get("/internal/pulse/time-in-pipeline", headers=HEADERS)
        .json()
    )
    flags = {group["key"]: group["low_sample"] for group in body["groups"]}
    assert flags == {"saas": True, "data": False}


def test_low_sample_cannot_be_overridden_by_caller() -> None:
    assert PipelineTimeGroup(key="x", n=2, low_sample=False).low_sample is True


def test_no_data_is_null_not_zero() -> None:
    client = _client(FakeRepository(None, empty=True))
    backlog = client.get("/internal/pulse/pipeline-backlog", headers=HEADERS).json()
    assert backlog["active"] is None
    assert backlog["hot"] is None
    assert backlog["capacity_estimate"] is None
    assert client.get("/internal/pulse/top-sources", headers=HEADERS).json()["ranking"] == []
    ops = client.get("/internal/pulse/ops/health", headers=HEADERS).json()
    assert ops["outbox_backlog"] is None


def test_null_money_metrics_stay_null() -> None:
    body = (
        _client(FakeRepository(datetime.now(UTC)))
        .get("/internal/pulse/source-efficiency", headers=HEADERS)
        .json()
    )
    row = body["sources"][0]
    assert row["cost_usd"] is None
    assert row["cost_per_meeting"] is None
    assert row["roas"] is None


def test_ops_health_marks_old_source_stale() -> None:
    body = (
        _client(FakeRepository(datetime.now(UTC)))
        .get("/internal/pulse/ops/health", headers=HEADERS)
        .json()
    )
    assert body["sources"][0]["stale"] is True
    assert body["outbox_backlog"] == 3


def test_error_responses_do_not_get_cache_header() -> None:
    response = _client(FakeRepository(None)).get("/internal/pulse/segments", headers=HEADERS)
    assert response.status_code == 501
    assert "cache-control" not in response.headers
