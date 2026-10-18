"""Offline end-to-end pipeline test (REQ-QA-004, ADR-IMT-0001 sections 7 and E1.9).

One mock Bright Data snapshot travels webhook, processor, entity resolution, company upsert and
signal creation, outbox relay, Pulse rollup, ML score, auto-qualification, and Brief, over
in-memory fake repositories and a fake Redis (``support_pipeline.World``). The real service code
runs on top, and every final read goes through the gateway ASGI app: ``/api/v1/leads``,
``/api/v1/leads/{id}``, ``/api/v1/brief/companies/{id}`` and ``/api/v1/pulse/*``. The strict
``xfail`` tests record production defects found while wiring the path; each one starts passing
(and then fails the run) when the owner fixes the defect.
"""

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("fakeredis")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
pytest.importorskip("jwt")

from support_pipeline import (  # noqa: E402
    DATASET_ID,
    EVENTS_STREAM,
    INGEST_STREAM,
    TENANT_ID,
    PermissiveLimiter,
    ScriptlessRedis,
    World,
)

from services.gateway.app import main as gw  # noqa: E402
from services.ingest.app import main as ingest_main  # noqa: E402

SECRET = "pipeline-webhook-secret"
FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "brightdata" / "synthetic" / "maps.json"
)
REAL_ASYNC_CLIENT = httpx.AsyncClient


def _snapshot_body(snapshot_id: str = "snap-maps-1") -> bytes:
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body = {"snapshot_id": snapshot_id, "dataset_id": DATASET_ID, "payload": records}
    return json.dumps(body).encode()


def _signed(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return {
        "Content-Type": "application/json",
        "X-BrightData-Timestamp": timestamp,
        "X-BrightData-Signature": digest.hexdigest(),
    }


async def _boot(
    monkeypatch: pytest.MonkeyPatch, asgi_client: Any, *, stamp_tenant: bool
) -> AsyncIterator[tuple[World, Any]]:
    redis = ScriptlessRedis(decode_responses=True)
    world = World(redis, stamp_tenant=stamp_tenant)
    monkeypatch.setattr(ingest_main.settings, "bright_data_webhook_secret", SECRET)
    monkeypatch.setattr(gw, "httpx", world.gateway_shim(REAL_ASYNC_CLIENT))
    monkeypatch.setattr(
        gw,
        "settings",
        gw.settings.model_copy(
            update={"upstream_retry_base_delay_seconds": 0, "upstream_retry_max_delay_seconds": 0}
        ),
    )
    async with asgi_client(gw.app) as client:
        gw.app.state.rate_limiter = PermissiveLimiter()
        try:
            yield world, client
        finally:
            await world.close()
            await redis.aclose()


@pytest.fixture
async def stack(
    monkeypatch: pytest.MonkeyPatch, asgi_client: Any
) -> AsyncIterator[tuple[World, Any]]:
    """World with the tenant shim for the ingest-to-core call, plus the gateway client."""
    async for pair in _boot(monkeypatch, asgi_client, stamp_tenant=True):
        yield pair


@pytest.fixture
async def unstamped_stack(
    monkeypatch: pytest.MonkeyPatch, asgi_client: Any
) -> AsyncIterator[tuple[World, Any]]:
    """World that uses the production ingest client as it is (no tenant on the upsert)."""
    async for pair in _boot(monkeypatch, asgi_client, stamp_tenant=False):
        yield pair


async def _post_snapshot(client: Any, body: bytes) -> Any:
    return await client.post("/api/v1/ingest/webhook/bd", content=body, headers=_signed(body))


async def _run_to_scored_leads(world: World, client: Any) -> dict[str, UUID]:
    """Drive every stage and return the company id by city after auto-qualification."""
    body = _snapshot_body()
    accepted = await _post_snapshot(client, body)
    assert accepted.status_code == 202, accepted.text
    assert await world.run_processor() == [2]
    assert await world.run_relay() == {"published": 4, "retried": 0, "dead_lettered": 0}
    await world.run_pulse_consumer()
    companies = {c["city"]: c["id"] for c in world.core_store.companies.values()}
    features = {"Medellin": (1.0, 1.0, 1.0), "Bogota": (0.6, 0.5, 0.5)}
    for city, (icp, signal, territory) in features.items():
        scored = await client.post(
            "/api/v1/scores",
            json={
                "company_id": str(companies[city]),
                "icp_fit": icp,
                "signal_strength": signal,
                "territory_opportunity": territory,
                "source_version": "2",
            },
        )
        assert scored.status_code == 200, scored.text
    await world.run_core_worker()
    return companies


# --- the full path -----------------------------------------------------------------------


async def test_webhook_is_verified_deduplicated_and_queued(stack: tuple[World, Any]) -> None:
    world, client = stack
    body = _snapshot_body()

    rejected = await client.post(
        "/api/v1/ingest/webhook/bd",
        content=body,
        headers=_signed(body) | {"X-BrightData-Signature": "0" * 64},
    )
    first = await _post_snapshot(client, body)
    again = await _post_snapshot(client, body)

    assert rejected.status_code == 401
    assert first.status_code == 202
    assert first.json()["status"] == "accepted" and first.json()["next_step"] == "processor_queued"
    assert again.json()["status"] == "duplicate" and again.json()["next_step"] == "already_queued"
    assert again.json()["raw_snapshot_id"] == first.json()["raw_snapshot_id"]
    queued = await world.redis.xrange(INGEST_STREAM)
    assert len(queued) == 1
    assert queued[0][1]["raw_snapshot_id"] == first.json()["raw_snapshot_id"]
    assert len(world.ingest_engine.snapshots) == 1


async def test_processor_resolves_entities_and_creates_companies_and_signals(
    stack: tuple[World, Any],
) -> None:
    world, client = stack
    await _post_snapshot(client, _snapshot_body())

    assert await world.run_processor() == [2]

    store = world.core_store
    companies = {c["city"]: c for c in store.companies.values()}
    assert set(companies) == {"Medellin", "Bogota"}
    medellin = companies["Medellin"]
    assert medellin["legal_name"] == "Cafe Sintetico"
    assert medellin["website"] == "https://cafe.example.com"
    assert medellin["source_flags"] == {"source_ids": {"maps": "syn-place-1"}}
    assert medellin["tenant_id"] == TENANT_ID and medellin["version"] == 1
    assert {s["external_id"] for s in store.signals.values()} == {"syn-place-1", "syn-place-2"}
    assert {s["signal_type"] for s in store.signals.values()} == {"maps_listing"}
    assert {s["source"] for s in store.signals.values()} == {"maps"}
    # Entity resolution: no RUES record, first attempt, so a new company queued for lookup.
    engine = world.ingest_engine
    assert {c["status"] for c in engine.candidates.values()} == {"pending_rues"}
    assert {c["resolution_attempts"] for c in engine.candidates.values()} == {1}
    assert len(engine.rues_queue) == 2 and engine.failed_records == []
    assert [(e["event_type"], e["tenant_id"]) for e in store.outbox] == [
        ("company.updated", TENANT_ID),
        ("signal.created", TENANT_ID),
        ("company.updated", TENANT_ID),
        ("signal.created", TENANT_ID),
    ]


async def test_relay_publishes_once_and_pulse_rolls_the_signals_up(
    stack: tuple[World, Any],
) -> None:
    world, client = stack
    await _post_snapshot(client, _snapshot_body())
    await world.run_processor()

    assert await world.run_relay() == {"published": 4, "retried": 0, "dead_lettered": 0}
    assert await world.run_relay() == {"published": 0, "retried": 0, "dead_lettered": 0}
    consumed = await world.run_pulse_consumer()

    entries = await world.redis.xrange(EVENTS_STREAM)
    assert [fields["event_type"] for _id, fields in entries] == [
        "company.updated",
        "signal.created",
        "company.updated",
        "signal.created",
    ]
    assert {fields["tenant_id"] for _id, fields in entries} == {str(TENANT_ID)}
    assert len(consumed) == 4
    assert len(world.pulse_db.signal_facts) == 2
    assert {f["tenant_id"] for f in world.pulse_db.signal_facts.values()} == {TENANT_ID}
    overview = await client.get("/api/v1/pulse/market/overview")
    assert overview.status_code == 200
    assert overview.headers["cache-control"]
    assert {k: overview.json()[k] for k in ("signals", "leads", "hot_leads")} == {
        "signals": 2,
        "leads": 0,
        "hot_leads": 0,
    }
    assert overview.json()["as_of"] is not None


async def test_score_event_qualifies_leads_and_the_api_serves_them(
    stack: tuple[World, Any],
) -> None:
    world, client = stack
    companies = await _run_to_scored_leads(world, client)

    scores = {c: world.core_store.scores[i] for c, i in companies.items()}
    assert (scores["Medellin"]["score"], scores["Medellin"]["is_hot"]) == (100, True)
    assert (scores["Bogota"]["score"], scores["Bogota"]["is_hot"]) == (55, False)
    assert scores["Medellin"]["score_version"] == "rule-f1:2"
    events = await world.redis.xrange(EVENTS_STREAM)
    assert [f["event_type"] for _i, f in events].count("lead.score_updated") == 2

    listed = await client.get("/api/v1/leads")
    assert listed.status_code == 200
    data = {row["legal_name"]: row for row in listed.json()["data"]}
    assert set(data) == {"Cafe Sintetico", "Tienda Sintetica"}
    assert listed.json()["next_cursor"] is None
    hot, qualified = data["Cafe Sintetico"], data["Tienda Sintetica"]
    assert (hot["stage"], hot["score"], hot["company_id"]) == (
        "hot",
        100,
        str(companies["Medellin"]),
    )
    assert (qualified["stage"], qualified["score"]) == ("qualified", 55)
    assert hot["service_line"] == "general" and hot["version"] == 3
    assert qualified["version"] == 2

    only_hot = await client.get("/api/v1/leads", params={"state": "hot"})
    assert [r["legal_name"] for r in only_hot.json()["data"]] == ["Cafe Sintetico"]
    strong = await client.get("/api/v1/leads", params={"min_score": 60})
    assert [r["legal_name"] for r in strong.json()["data"]] == ["Cafe Sintetico"]

    detail = await client.get(f"/api/v1/leads/{hot['id']}")
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"3"'
    assert detail.json()["source"] == "auto_qualification"
    assert detail.json()["qualification_reason"] == {"score": 100}
    assert detail.json()["stage"] == "hot"
    assert (await client.get(f"/api/v1/leads/{UUID(int=7)}")).status_code == 404

    audit = [(a["payload"]["from"], a["payload"]["to"]) for a in world.core_store.lead_audit]
    assert audit.count(("discovered", "qualified")) == 2
    assert audit.count(("qualified", "hot")) == 1


async def test_new_leads_are_relayed_with_their_tenant(stack: tuple[World, Any]) -> None:
    world, client = stack
    await _run_to_scored_leads(world, client)

    assert await world.run_relay() == {"published": 2, "retried": 0, "dead_lettered": 0}

    assert all(row["published_at"] for row in world.core_store.outbox)
    created = [r for r in world.core_store.outbox if r["event_type"] == "lead.created"]
    assert len(created) == 2 and {r["tenant_id"] for r in created} == {TENANT_ID}
    assert {r["payload"]["origin"] for r in created} == {"auto_qualification"}
    entries = await world.redis.xrange(EVENTS_STREAM)
    relayed = [fields for _id, fields in entries if fields["event_type"] == "lead.created"]
    assert len(relayed) == 2 and {f["tenant_id"] for f in relayed} == {str(TENANT_ID)}


async def test_brief_for_a_company_is_generated_from_the_pipeline_facts(
    stack: tuple[World, Any],
) -> None:
    world, client = stack
    companies = await _run_to_scored_leads(world, client)
    company_id = companies["Medellin"]

    first = await client.get(f"/api/v1/brief/companies/{company_id}")

    assert first.status_code == 200, first.text
    assert first.headers["x-brief-cache"] == "hit"
    body = first.json()
    assert body["company_id"] == str(company_id)
    assert body["stale"] is False and body["degraded"] is False
    assert body["source_version"] == 2  # company version 1 + one signal of version 1
    assert body["summarizer_version"]
    content = body["content"]
    assert content["headline"] == "Unknown sector - Medellin - Score 100 - 1 active signals"
    assert content["next_steps"] == ["Hand the hot lead to the strategist for approval."]
    assert content["coverage"] == {"company": True, "rues": False, "activity": False}

    cached = await client.get(f"/api/v1/brief/companies/{company_id}")
    assert cached.json()["generated_at"] == body["generated_at"]
    missing = await client.get(f"/api/v1/brief/companies/{UUID(int=9)}")
    assert missing.status_code == 404


async def test_pulse_endpoints_answer_through_the_gateway(stack: tuple[World, Any]) -> None:
    world, client = stack
    await _run_to_scored_leads(world, client)

    for path in (
        "unprocessed-signals",
        "pipeline-backlog",
        "source-efficiency",
        "time-in-pipeline",
        "top-sources",
        "ops/health",
    ):
        response = await client.get(f"/api/v1/pulse/{path}")
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert isinstance(body["as_of"], int) and body["stale"] is False
        assert response.headers["x-as-of"] == str(body["as_of"])
        assert response.headers["cache-control"]
    overview = await client.get("/api/v1/pulse/market/overview")
    assert overview.json()["signals"] == 2


# --- production defects found while wiring the path (strict xfail) ------------------------


@pytest.mark.xfail(
    strict=True,
    reason="D-1: ingest upserts companies without tenant_id, so every outbox event of an "
    "ingested company is dead-lettered as missing_tenant_id and never reaches Pulse",
)
async def test_defect_ingested_companies_reach_the_stream(
    unstamped_stack: tuple[World, Any],
) -> None:
    world, client = unstamped_stack
    await _post_snapshot(client, _snapshot_body())
    await world.run_processor()

    counts = await world.run_relay()

    assert counts == {"published": 4, "retried": 0, "dead_lettered": 0}


@pytest.mark.xfail(
    strict=True,
    reason="D-2: the lead brief route passes the lead id to Brief as a company id, so core "
    "cannot find the company and the endpoint answers 404 for an existing lead",
)
async def test_defect_lead_brief_resolves_the_lead_company(stack: tuple[World, Any]) -> None:
    world, client = stack
    companies = await _run_to_scored_leads(world, client)
    lead = next(
        row
        for row in (await client.get("/api/v1/leads")).json()["data"]
        if row["company_id"] == str(companies["Medellin"])
    )

    response = await client.get(f"/api/v1/leads/{lead['id']}/brief")

    assert response.status_code == 200
    assert response.json()["lead_id"] == lead["id"]
    assert response.json()["company_id"] == str(companies["Medellin"])


@pytest.mark.xfail(
    strict=True,
    reason="D-3: core has no GET /internal/core/companies/{id}/brief-facts route, so the "
    "production HttpCoreSource cannot read facts from core",
)
async def test_defect_core_serves_brief_facts(stack: tuple[World, Any]) -> None:
    world, client = stack
    companies = await _run_to_scored_leads(world, client)
    url = f"http://localhost:{world.ports['core']}/internal/core/companies/"
    async with httpx.AsyncClient(transport=world.transport) as core:
        response = await core.get(
            f"{url}{companies['Medellin']}/brief-facts",
            headers={"X-Service-Token": "test-internal-token"},
        )

    assert response.status_code == 200


@pytest.mark.xfail(
    strict=True,
    reason="D-4: ML publishes lead.score_updated straight to invent.events without a payload "
    "or tenant_id field, so the Pulse consumer raises KeyError on it and, because pending "
    "entries are re-read first, stays stuck on that entry",
)
async def test_defect_pulse_consumer_survives_score_events(stack: tuple[World, Any]) -> None:
    world, client = stack
    await _run_to_scored_leads(world, client)
    await world.run_relay()

    consumed = await world.run_pulse_consumer()

    assert [f["event_type"] for f in consumed].count("lead.created") == 2
