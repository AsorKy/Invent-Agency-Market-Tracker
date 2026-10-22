"""ML resolves tenant score weights through core, never through SQL (T-144, INV-01)."""

import re
from pathlib import Path
from uuid import uuid4

import fakeredis
import httpx
import pytest

from libs.common.internal_auth import verify_service_token
from libs.common.settings import get_settings
from libs.contracts.api import ScoreFeatures
from services.ml.app.core_rules import CoreRulesUnavailable, HttpCoreRules
from services.ml.app.repository import MlRepository
from services.ml.app.scoring_service import ScoringService
from tests.unit.support_cov_services import FakeRedisWrapper

TENANT = uuid4()
COMPANY = uuid4()


class Store:
    async def upsert_score(self, **_kw) -> None:
        return None

    async def latest_score(self, _company_id):
        return None


class FakeRules:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows, self.error = rows or [], error

    async def get_rules(self, _tenant_id):
        if self.error:
            raise self.error
        return self.rows

    async def close(self) -> None:
        return None


def _repo(rules) -> MlRepository:
    repo = MlRepository.__new__(MlRepository)
    repo.engine = None
    repo._core_rules = rules
    return repo


async def _score(repo: MlRepository):
    redis = FakeRedisWrapper(fakeredis.FakeAsyncRedis(decode_responses=True))
    service = ScoringService(Store(), redis, lock_ttl_seconds=5)
    weights = await repo.load_weights(TENANT)
    features = ScoreFeatures(
        company_id=COMPANY,
        icp_fit=0.9,
        signal_strength=0.8,
        territory_opportunity=0.7,
        source_version="3",
    )
    return await service.regenerate(features, weights, TENANT)


async def test_tenant_rules_give_tenant_source() -> None:
    response = await _score(_repo(FakeRules([("icp_fit", 0.9, True)])))
    assert response.rules_source == "tenant"
    assert response.score_version == "rule-f1:3"


async def test_unavailable_core_gives_default_and_never_fails(caplog) -> None:
    with caplog.at_level("WARNING"):
        response = await _score(_repo(FakeRules(error=CoreRulesUnavailable("down"))))
    assert response.rules_source == "default"
    assert response.score_version == "rule-f1-default:3"
    assert "using default weights" in caplog.text


async def test_no_client_gives_default() -> None:
    assert (await _score(_repo(None))).rules_source == "default"


def _client(handler) -> HttpCoreRules:
    rules = HttpCoreRules("http://core", "secret", timeout_seconds=2.0)
    rules._client = httpx.AsyncClient(
        base_url="http://core", transport=httpx.MockTransport(handler), timeout=2.0
    )
    return rules


async def test_http_client_signs_token_and_sends_tenant() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"rules": [{"rule_name": "icp_fit", "weight": 0.4, "active": True}], "version": 7},
        )

    rows = await _client(handler).get_rules(TENANT)
    assert rows == [("icp_fit", 0.4, True)]
    request = seen[0]
    assert request.url.path == "/internal/core/admin/score-rules"
    assert request.headers["X-Tenant-Id"] == str(TENANT)
    assert verify_service_token(request.headers["X-Service-Token"], "secret") == "ml"


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(503), httpx.Response(403), httpx.Response(200, json={})],
)
async def test_http_client_failures_are_unavailable(response: httpx.Response) -> None:
    with pytest.raises(CoreRulesUnavailable):
        await _client(lambda _r: response).get_rules(TENANT)


async def test_http_client_transport_error_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(CoreRulesUnavailable):
        await _client(boom).get_rules(TENANT)


def test_timeout_is_explicit() -> None:
    assert (
        HttpCoreRules("http://core", get_settings().internal_service_token)._client.timeout.read
        == 2.0
    )


def test_no_ml_file_uses_core_schema_in_sql() -> None:
    root = Path(__file__).resolve().parents[2] / "services" / "ml"
    pattern = re.compile(r"\b(FROM|JOIN|INTO|UPDATE|TABLE)\s+core\.", re.IGNORECASE)
    offenders = [
        str(path) for path in root.rglob("*.py") if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
