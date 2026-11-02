"""A real-looking Bright Data company gets a score and an automatic lead (REQ-COR-008, D-23).

The companies are built only from the importer contract fields: Maps listings (category, rating,
reviews_count, city, latitude/longitude, website, phone), a LinkedIn job posting and a decision
maker signal, with no RUES data (no NIT). They run through the real ingest ``SnapshotFlow`` (with
inline contract mappings, so the test does not depend on the provider YAML files), the seed
tracker that builds the score profiles, the demo heuristic of ``libs/domain/score_features.py``,
the F1 rule score, and core's automatic qualification. No network or database is used.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from libs.contracts.api import ScoreFeatures  # noqa: E402
from libs.domain.entity_resolution import (  # noqa: E402
    EntityResolver,
    InMemoryResolutionStore,
    MockRuesLookup,
)
from libs.domain.pipeline import PipelineStage  # noqa: E402
from libs.domain.score_features import score_features  # noqa: E402
from libs.domain.scoring import calculate_rule_score  # noqa: E402
from ops.seed.enrich import TrackingCore  # noqa: E402
from services.core.app.qualification import handle_score_event  # noqa: E402
from services.core.app.retention import reject_personal_identifiers  # noqa: E402
from services.ingest.app.mapping import parse_mapping  # noqa: E402
from services.ingest.app.to_core import Registered, SnapshotFlow  # noqa: E402
from tests.unit.test_auto_qualification import FakeUow, Store  # noqa: E402

NOW = datetime(2026, 11, 2, 15, tzinfo=UTC)
TENANT = UUID("00000000-0000-4000-8000-0000000000a1")


def _mapping(source: str, entity: str, signal_type: str, fields: dict[str, Any]) -> Any:
    rules = {name: {"from": [name], **extra} for name, extra in fields.items()}
    return parse_mapping(
        {
            "version": 1,
            "source": source,
            "entity": entity,
            "signal_type": signal_type,
            "external_id": ["id"],
            "fields": rules,
        }
    )


MAPPINGS = {
    "maps": _mapping(
        "maps",
        "company",
        "maps_listing",
        {
            "name": {"required": True},
            "category": {"transform": "lower"},
            "rating": {"transform": "float"},
            "reviews_count": {"transform": "int"},
            "city": {},
            "latitude": {"transform": "float"},
            "longitude": {"transform": "float"},
            "website": {"transform": "url"},
            "phone": {},
        },
    ),
    "linkedin_jobs": _mapping(
        "linkedin_jobs",
        "job_posting",
        "job_posting",
        {"title": {"required": True}, "company_name": {"required": True}, "city": {}},
    ),
}
MAPS = [
    {
        "id": "m1",
        "name": "Restaurante El Poblado SAS",
        "category": "Restaurante",
        "rating": 4.6,
        "reviews_count": 230,
        "city": "Medellin",
        "latitude": 6.2088,
        "longitude": -75.5676,
        "website": "https://elpoblado.example.co",
        "phone": "+57 604 444 0000",
    },
    {
        "id": "m2",
        "name": "Ferreteria La 80",
        "category": "Ferreteria",
        "rating": 4.1,
        "reviews_count": 12,
        "city": "Medellin",
        "latitude": 6.25,
        "longitude": -75.6,
    },
    {
        "id": "m3",
        "name": "Software Andino SAS",
        "category": "Empresa de software",
        "rating": 4.9,
        "reviews_count": 41,
        "city": "Bogota",
        "website": "https://andino.example.co",
    },
]
JOB = {
    "id": "j1",
    "title": "Desarrollador backend",
    "company_name": "Software Andino SAS",
    "city": "Bogota",
}


class ContractCore:
    """Core double: one company per legal name and city, like the importer's link contract."""

    def __init__(self) -> None:
        self.ids: dict[tuple[str, str | None], str] = {}
        self.signals: list[dict[str, Any]] = []

    async def upsert_company(self, body: dict[str, Any]) -> dict[str, Any]:
        reject_personal_identifiers(body)
        key = (body["legal_name"], body.get("city"))
        assert "nit" not in body  # no RUES data: the company is RUES pending in core
        company_id = self.ids.setdefault(key, str(uuid4()))
        return {"company_id": company_id, "status": "created", "version": 1}

    async def create_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        reject_personal_identifiers(body)  # core answers 422 on personal identifiers
        self.signals.append(body)
        return {"signal_id": str(uuid4()), "status": "created"}


class NoFailures:
    async def record(self, **kwargs: Any) -> None:
        raise AssertionError(f"unexpected import failure: {kwargs['reason']}")


async def _register(name: str, city: str | None, nit: str | None) -> Registered:
    return Registered(id=str(uuid4()))


async def test_real_looking_companies_get_a_score_and_a_discovered_or_qualified_lead() -> None:
    core = TrackingCore(ContractCore())
    resolver = EntityResolver(MockRuesLookup([]), InMemoryResolutionStore(), now=NOW)
    flow = SnapshotFlow(
        core=core,
        failures=NoFailures(),
        resolve=resolver.resolve,
        dataset_sources={},
        tenant_id=TENANT,
        mappings=MAPPINGS,
        retry_attempts=1,
    )
    for record in MAPS:
        outcome = await flow.process_record(
            source="maps", snapshot_id="s1", record=record, register=_register, observed_at=NOW
        )
        assert outcome.status == "delivered"
    job = await flow.process_record(
        source="linkedin_jobs",
        snapshot_id="s2",
        record=JOB,
        register=_register,
        observed_at=NOW,
    )
    assert job.status == "delivered"
    ids = core.inner.ids
    poblado = ids[("Restaurante El Poblado SAS", "Medellin")]
    # The importer links decision makers to the company as core signals (INV-12 fields only).
    await core.create_signal(
        {
            "company_id": poblado,
            "source": "business_search_people",
            "signal_type": "decision_maker",
            "observed_at": NOW.isoformat(),
            "payload": {
                "name": "Ana Restrepo",
                "title": "Gerente general",
                "profile_url": "https://www.linkedin.com/in/ana-restrepo-example",
                "source": "business_search_people",
                "found_at": NOW.isoformat(),
            },
            "tenant_id": str(TENANT),
        }
    )
    maps_payload = core.inner.signals[0]["payload"]
    assert {"category", "rating", "reviews_count", "city", "latitude", "website", "phone"} <= set(
        maps_payload
    )

    rows = score_features(core.profiles)
    assert len(rows) == 3  # every company got features, none needed a NIT
    store = Store()
    store.companies = {UUID(company_id): TENANT for company_id in ids.values()}
    scores: dict[str, int] = {}
    for row in rows:
        features = ScoreFeatures.model_validate(row)  # the body ml accepts
        breakdown = calculate_rule_score(
            icp_fit=features.icp_fit,
            signal_strength=features.signal_strength,
            territory_opportunity=features.territory_opportunity,
        )
        scores[str(features.company_id)] = breakdown.score
        event = {
            "event_id": str(uuid4()),
            "event_type": "lead.score_updated",
            "aggregate_id": str(features.company_id),
            "score": str(breakdown.score),
            "tenant_id": str(TENANT),
        }
        assert await handle_score_event(FakeUow(store), event, now=NOW) == "created"

    # Medellin is the widest city (2 companies); no company has a NIT, so icp_fit <= 0.7.
    andino = ids[("Software Andino SAS", "Bogota")]
    ferreteria = ids[("Ferreteria La 80", "Medellin")]
    assert scores == {poblado: 64, andino: 54, ferreteria: 44}
    stages = {str(lead["company_id"]): lead["stage"] for lead in store.leads.values()}
    assert stages == {
        poblado: PipelineStage.QUALIFIED.value,  # website + decision maker, widest city
        andino: PipelineStage.QUALIFIED.value,  # website + job posting
        ferreteria: PipelineStage.DISCOVERED.value,  # no website, one listing
    }
