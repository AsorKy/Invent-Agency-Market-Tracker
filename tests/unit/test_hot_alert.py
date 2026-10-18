"""Unit tests for the hot lead alert (REQ-ALR-001).

A fake clock, a fake webhook poster and fake units of work prove: one alert per lead and
version, backoff and the 60 second bound, no effect on the commit when the webhook fails, and
the two Hot paths (auto qualification and a manual status change). No network is used.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from libs.domain.pipeline import PipelineStage as S  # noqa: E402
from services.core.app.hot_alert import HotAlert, HotAlertNotifier  # noqa: E402
from services.core.app.leads import LeadStatusPatch, change_status  # noqa: E402
from services.core.app.qualification import handle_score_event  # noqa: E402

TENANT = uuid4()
COMPANY = uuid4()
URL = "http://mock-telegram/hot"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class Webhook:
    """Fails the first ``failures`` posts, then succeeds; each post takes one second."""

    def __init__(self, clock: Clock, failures: int = 0) -> None:
        self.clock, self.failures = clock, failures
        self.posts: list[tuple[float, dict[str, Any]]] = []

    async def __call__(self, url: str, body: dict[str, Any]) -> bool:
        assert url == URL
        self.clock.now += 1.0
        self.posts.append((self.clock.now, body))
        return len(self.posts) > self.failures


def _alert(clock: Clock, version: int = 3, lead_id: UUID | None = None) -> HotAlert:
    return HotAlert(TENANT, lead_id or uuid4(), COMPANY, version, 90, clock())


def _setup(failures: int = 0) -> tuple[Clock, Webhook, HotAlertNotifier]:
    clock = Clock()
    hook = Webhook(clock, failures)
    return clock, hook, HotAlertNotifier(URL, poster=hook, clock=clock, sleep=clock.sleep)


def test_delivered_once_per_lead_and_version() -> None:
    clock, hook, notifier = _setup()
    alert = _alert(clock)

    async def run() -> None:
        await notifier(alert)
        await notifier(alert)
        await notifier.drain()
        await notifier(alert)
        await notifier(_alert(clock, version=5, lead_id=alert.lead_id))
        await notifier.drain()

    asyncio.run(run())
    assert [p[1]["version"] for p in hook.posts] == [3, 5]
    assert hook.posts[0][1]["tenant_id"] == str(TENANT)
    assert hook.posts[0][1]["stage"] == "Hot"


def test_retries_with_backoff_inside_sixty_seconds() -> None:
    clock, hook, notifier = _setup(failures=3)
    alert = _alert(clock)
    start = alert.committed_at
    assert asyncio.run(notifier.deliver(alert)) is True
    assert len(hook.posts) == 4
    assert hook.posts[-1][0] - start <= 60.0


def test_gives_up_at_the_deadline_and_allows_a_later_retry() -> None:
    clock, hook, notifier = _setup(failures=10_000)
    alert = _alert(clock)
    assert asyncio.run(notifier.deliver(alert)) is False
    assert clock.now - alert.committed_at <= 60.0
    assert len(hook.posts) > 3


def test_no_url_disables_alerts() -> None:
    clock = Clock()
    hook = Webhook(clock)
    notifier = HotAlertNotifier(None, poster=hook, clock=clock, sleep=clock.sleep)
    asyncio.run(notifier(_alert(clock)))
    assert hook.posts == []


class Uow:
    """One fake unit of work for both the qualification and the lead status paths."""

    def __init__(self, leads: dict[UUID, dict[str, Any]]) -> None:
        self.leads = leads

    async def __aenter__(self) -> "Uow":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def mark_processed(self, event_id: UUID) -> bool:
        return True

    async def lock_key(self, key: str) -> None:
        return None

    async def get_company_tenant(self, company_id: UUID) -> tuple[bool, UUID | None]:
        return True, TENANT

    async def get_config(self, tenant_id: UUID) -> dict[str, Any] | None:
        return None

    async def find_leads(self, company_id: UUID) -> list[dict[str, Any]]:
        return [dict(r) for r in self.leads.values()]

    async def has_manual_transition(self, lead_id: UUID) -> bool:
        return False

    async def insert_lead(self, row: dict[str, Any]) -> None:
        self.leads[row["id"]] = dict(row)

    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None:
        return dict(self.leads[lead_id]) if lead_id in self.leads else None

    async def update_stage(self, row: dict[str, Any]) -> None:
        self.leads[row["id"]].update(row)

    async def add_audit(self, **kw: Any) -> None:
        return None

    async def add_outbox_event(self, **kw: Any) -> None:
        return None


class Recorder:
    def __init__(self) -> None:
        self.alerts: list[HotAlert] = []

    async def __call__(self, alert: HotAlert) -> None:
        self.alerts.append(alert)


def _score(score: int) -> dict[str, str]:
    return {
        "event_id": str(uuid4()),
        "event_type": "lead.score_updated",
        "aggregate_id": str(COMPANY),
        "score": str(score),
        "tenant_id": str(TENANT),
    }


def test_auto_qualification_to_hot_alerts_with_final_version() -> None:
    leads: dict[UUID, dict[str, Any]] = {}
    rec = Recorder()
    asyncio.run(handle_score_event(Uow(leads), _score(95), on_hot=rec))
    (lead,) = leads.values()
    (alert,) = rec.alerts
    assert lead["stage"] == S.HOT.value
    assert (alert.lead_id, alert.version, alert.tenant_id) == (lead["id"], 3, TENANT)
    rec.alerts.clear()
    asyncio.run(handle_score_event(Uow(leads), _score(20), on_hot=rec))
    assert rec.alerts == []


def test_manual_move_to_hot_alerts_and_other_stages_do_not() -> None:
    lead_id = uuid4()
    row = {"id": lead_id, "tenant_id": TENANT, "company_id": COMPANY, "version": 2}
    leads = {lead_id: {**row, "stage": S.DISCOVERED.value}}
    rec = Recorder()

    def patch(stage: S, version: int) -> LeadStatusPatch:
        return LeadStatusPatch(stage=stage, expected_version=version)

    asyncio.run(change_status(Uow(leads), lead_id, patch(S.QUALIFIED, 2), on_hot=rec))
    assert rec.alerts == []
    asyncio.run(change_status(Uow(leads), lead_id, patch(S.HOT, 3), on_hot=rec))
    (alert,) = rec.alerts
    assert (alert.lead_id, alert.version, alert.tenant_id) == (lead_id, 4, TENANT)


def test_failing_callback_never_breaks_the_commit() -> None:
    async def boom(alert: HotAlert) -> None:
        raise RuntimeError("webhook down")

    leads: dict[UUID, dict[str, Any]] = {}
    assert asyncio.run(handle_score_event(Uow(leads), _score(95), on_hot=boom)) == "created"
    assert next(iter(leads.values()))["stage"] == S.HOT.value
