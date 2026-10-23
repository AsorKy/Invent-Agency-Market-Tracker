"""Prove the wired event chain producers are flag-gated and safe (T-159, REQ-LNS-012).

The real ``create_signal``, ``create_lead`` and ``change_status`` code runs over the offline
core engine; ``ScoringService`` runs on fakeredis. With ``EVENT_CHAIN_EMIT`` off the outbox and the
stream hold exactly today's events. With it on every chain event reaches the relay, the Pulse
consumer and the ML consumer without a dead letter, and each contiguous segment traces.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("sqlalchemy")
pytest.importorskip("structlog")
pytest.importorskip("redis.asyncio")
pytest.importorskip("fakeredis")

from support_relay import FakeCoreEngine, FakePulseDb, Result  # noqa: E402
from test_ml_outbox import FakeMlOutbox  # noqa: E402

from libs.common.settings import get_settings  # noqa: E402
from libs.contracts.api import ScoreFeatures, SignalCreate  # noqa: E402
from libs.domain.event_chain import (  # noqa: E402
    CHAIN,
    chain_events_after,
    reference_parent,
    trace_chain,
)  # noqa: E402
from libs.domain.pipeline import PipelineStage  # noqa: E402
from services.core.app import leads as leads_module  # noqa: E402
from services.core.app import repository as repository_module  # noqa: E402
from services.core.app.dead_letter import relay_due_events, stream_fields  # noqa: E402
from services.core.app.leads import (  # noqa: E402
    LeadCreate,
    LeadStatusPatch,
    change_status,
    create_lead,
)
from services.core.app.repository import CoreRepository  # noqa: E402
from services.ml.app import consumer as ml_consumer  # noqa: E402
from services.ml.app import scoring_service as scoring_module  # noqa: E402
from services.ml.app.scoring_service import STREAM_NAME, ScoringService  # noqa: E402
from services.pulse.app.consumer import process_event  # noqa: E402
from tests.unit.support_cov_services import FakeRedisWrapper  # noqa: E402

TENANT = uuid4()
OBSERVED = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)
CHAIN_ONLY = set(CHAIN) - {"signal.created", "funnel.gate_decision"}


class Engine(FakeCoreEngine):
    """Add the two statements ``change_status`` runs that the shared fake does not know."""

    def _dispatch(self, sql: str, params: dict[str, Any], staged: Any) -> Result:
        if "UPDATE core.lead SET stage" in sql:
            staged.append(lambda: self.leads[params["id"]].update(params))
            return Result()
        if "INSERT INTO core.audit_log" in sql:
            return Result()
        return super()._dispatch(sql, params, staged)


class Store:
    """Score store that keeps the ml outbox rows written with the score (T-176)."""

    def __init__(self) -> None:
        self.outbox = FakeMlOutbox()

    async def upsert_score(self, **kw: Any) -> None:
        self.outbox.add(kw.get("outbox_events") or [])

    async def relay(self, redis: Any) -> dict[str, int]:
        """Run the ml relay instance over the rows; publish them to the stream."""

        async def publish(row: dict[str, Any]) -> None:
            await redis.xadd(STREAM_NAME, stream_fields(row))

        return await relay_due_events(self.outbox.unit_of_work(), publish)

    async def latest_score(self, _company_id: UUID) -> None:
        return None


def set_flag(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    settings = get_settings().model_copy(update={"event_chain_emit": enabled})
    for module in (repository_module, leads_module, scoring_module):
        monkeypatch.setattr(module, "get_settings", lambda: settings)


class Flow:
    def __init__(self, redis: Any) -> None:
        self.engine = Engine()
        self.repo = CoreRepository(None)
        self.repo.engine = self.engine  # type: ignore[assignment]
        self.company = uuid4()
        self.engine.add_company(self.company, TENANT)
        self.redis = redis

    async def run(self) -> None:
        signal = SignalCreate(
            company_id=self.company,
            source="rues",
            signal_type="new_company",
            observed_at=OBSERVED,
            source_external_id="sig-1",
        )
        await self.repo.create_signal(signal, request_id=None)
        body = LeadCreate(company_id=self.company, service_line="web", city="Bogota")
        created, _ = await create_lead(
            self.repo.lead_unit_of_work(), body, tenant_id=TENANT, idempotency_key="k1"
        )
        await change_status(
            self.repo.lead_unit_of_work(),
            UUID(created["lead_id"]),
            LeadStatusPatch(stage=PipelineStage.QUALIFIED, expected_version=1),
            tenant_id=TENANT,
        )
        self.store = Store()
        service = ScoringService(self.store, FakeRedisWrapper(self.redis), lock_ttl_seconds=5)  # type: ignore[arg-type]
        features = ScoreFeatures(
            company_id=self.company, icp_fit=0.9, signal_strength=0.8, territory_opportunity=0.7
        )
        await service.regenerate(features, None, TENANT)
        await self.store.relay(self.redis)

    def types(self) -> list[str]:
        return [row["event_type"] for row in self.engine.outbox]


async def stream_entries(redis: Any) -> list[dict[str, str]]:
    return [fields for _id, fields in await redis.xrange(STREAM_NAME)]


async def test_flag_off_keeps_the_outbox_and_stream_as_before(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    set_flag(monkeypatch, False)
    flow = Flow(fake_redis)
    await flow.run()
    assert flow.types() == ["signal.created", "lead.created"]
    for row in flow.engine.outbox:
        assert "source_version" not in row["payload"] and "parent" not in row["payload"]
    entries = await stream_entries(fake_redis)
    assert [e["event_type"] for e in entries] == ["lead.score_updated"]


async def test_flag_on_emits_every_producer_and_no_consumer_dead_letters(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    set_flag(monkeypatch, True)
    flow = Flow(fake_redis)
    await flow.run()
    assert flow.types() == [
        "signal.created",
        "signal.tagged",
        "lead.created",
        "company.matched",
        "signal.explained",
        "project.pipeline_updated",
        "project.forecast_updated",
    ]
    scored = [e for e in await stream_entries(fake_redis) if e["event_type"] == "signal.scored"]
    assert len(scored) == 1
    emitted = {row["event_type"] for row in flow.engine.outbox} | {"signal.scored"}
    assert emitted >= CHAIN_ONLY
    assert all(row["tenant_id"] == TENANT for row in flow.engine.outbox)

    async def publish(row: dict[str, Any]) -> None:
        await fake_redis.xadd(STREAM_NAME, stream_fields(row))

    counts = await relay_due_events(flow.repo.outbox_unit_of_work(), publish)
    assert counts == {"published": 7, "retried": 0, "dead_lettered": 0}
    assert flow.engine.dead_letters == []

    pulse = FakePulseDb()
    seen: list[str] = []
    for fields in await stream_entries(fake_redis):
        seen.append(fields["event_type"])
        async with pulse.begin() as connection:
            await process_event(connection, fields)  # type: ignore[arg-type]
        class Conn:
            async def execute(self, statement: Any, params: Any = None) -> Any:
                return type("R", (), {"scalar_one_or_none": lambda self: uuid4()})()

        await ml_consumer.process_event(Conn(), fields)  # type: ignore[arg-type]
    assert CHAIN_ONLY <= set(seen)


async def test_chain_segments_link_through_parents(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    set_flag(monkeypatch, True)
    flow = Flow(fake_redis)
    await flow.run()
    rows = {r["event_type"]: r for r in flow.engine.outbox}
    created, tagged = rows["signal.created"], rows["signal.tagged"]
    assert tagged["payload"]["parent"]["event_id"] == str(created["event_id"])
    assert tagged["payload"]["source_version"].startswith("core-chain-v1<")
    events = [
        {"event_id": str(r["event_id"]), "event_type": r["event_type"], "payload": r["payload"]}
        for r in (created, tagged)
    ]
    trace = trace_chain(events)
    assert trace.path == ("signal.created", "signal.tagged") and not trace.problems
    explained = rows["signal.explained"]["payload"]
    assert explained["parent"]["event_id"] == str(rows["company.matched"]["event_id"])
    forecast = rows["project.forecast_updated"]["payload"]
    assert forecast["parent"]["event_id"] == str(rows["project.pipeline_updated"]["event_id"])
    assert (
        rows["company.matched"]["payload"]["parent"]["event_id"]
        == (reference_parent("signal.scored", str(flow.company))["event_id"])
    )


async def test_full_chain_closes_with_real_event_ids(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    set_flag(monkeypatch, True)
    flow = Flow(fake_redis)
    signal = SignalCreate(
        company_id=flow.company,
        source="rues",
        signal_type="new_company",
        observed_at=OBSERVED,
        source_external_id="sig-full",
    )
    await flow.repo.create_signal(signal, request_id=None)
    tagged = next(r for r in flow.engine.outbox if r["event_type"] == "signal.tagged")
    store = Store()
    service = ScoringService(store, FakeRedisWrapper(fake_redis), lock_ttl_seconds=5)  # type: ignore[arg-type]
    features = ScoreFeatures(
        company_id=flow.company,
        icp_fit=0.9,
        signal_strength=0.8,
        territory_opportunity=0.7,
        parent_event_id=tagged["event_id"],
    )
    await service.regenerate(features, None, TENANT)
    await store.relay(fake_redis)
    scored = next(e for e in await stream_entries(fake_redis) if e["event_type"] == "signal.scored")
    scored_payload = json.loads(scored["payload"])
    assert scored_payload["parent_event_id"] == str(tagged["event_id"])
    body = LeadCreate(
        company_id=flow.company, service_line="web", scored_event_id=UUID(scored["event_id"])
    )
    created, _ = await create_lead(
        flow.repo.lead_unit_of_work(), body, tenant_id=TENANT, idempotency_key="k-full"
    )
    explained = next(r for r in flow.engine.outbox if r["event_type"] == "signal.explained")
    gate = chain_events_after(
        {
            "event_id": str(explained["event_id"]),
            "event_type": "signal.explained",
            "source_version": explained["payload"]["source_version"],
        },
        [("funnel.gate_decision", {"verdict": "go"}, "funnel-v1")],
        enabled=True,
    )[0]
    await change_status(
        flow.repo.lead_unit_of_work(),
        UUID(created["lead_id"]),
        LeadStatusPatch(
            stage=PipelineStage.QUALIFIED,
            expected_version=1,
            gate_event_id=UUID(gate["event_id"]),
        ),
        tenant_id=TENANT,
    )
    events = [
        {"event_id": str(r["event_id"]), "event_type": r["event_type"], "payload": r["payload"]}
        for r in flow.engine.outbox
    ]
    events.append(
        {"event_id": scored["event_id"], "event_type": "signal.scored", "payload": scored_payload}
    )
    events.append(gate)
    for event in events:
        if event["event_type"] in CHAIN:
            assert "parent_event_id" in event["payload"]
    trace = trace_chain(events)
    assert trace.complete is True and trace.gap is None, trace
