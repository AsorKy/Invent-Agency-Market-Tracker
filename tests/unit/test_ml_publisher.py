"""Tests for the ML publisher tenant header (REQ-LNS-004, REQ-SEC-005)."""

import asyncio
from typing import Any

import pytest

from libs.common.internal_auth import verify_service_token
from services.ml.app.publisher import (
    PATHS,
    CommunityResult,
    ProfileResult,
    PulsePublisher,
    TopicResult,
)

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, str]]] = []

    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> FakeResponse:
        self.calls.append((url, json, headers))
        return FakeResponse()


def test_all_kinds_send_tenant_and_token() -> None:
    client = FakeClient()
    pub = PulsePublisher(client, "s3cret", tenant_id=TENANT)

    async def run() -> None:
        await pub.publish_profiles(ProfileResult(run_id="r", assignments={"a": 0}))
        await pub.publish_communities(
            CommunityResult(run_id="r", communities={"a": 0}, centrality={"a": 1.0})
        )
        await pub.publish_topics(TopicResult(run_id="r", period="2026-09"))

    asyncio.run(run())
    assert [c[0] for c in client.calls] == [PATHS[k] for k in ("profiles", "communities", "topics")]
    for _, _, headers in client.calls:
        assert headers["X-Tenant-Id"] == TENANT
        assert verify_service_token(headers["X-Service-Token"], "s3cret") == "ml"


def test_missing_tenant_raises_before_any_call() -> None:
    client = FakeClient()
    pub = PulsePublisher(client, "s3cret")
    with pytest.raises(ValueError, match="tenant_id"):
        asyncio.run(pub.publish_profiles(ProfileResult(run_id="r", assignments={})))
    assert client.calls == []
