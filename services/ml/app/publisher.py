"""Publish D2/D3 results to ``pulse`` through its API (REQ-LNS-004).

Payload models live here because ``pulse`` has no ingest endpoint yet (gap
reported to the coordinator); the paths in ``PATHS`` are the proposed contract.
``PulsePublisher`` signs a fresh service token per request
(``libs/common/internal_auth.py``), sends the tenant of the batch in
``X-Tenant-Id`` (a missing tenant raises before any call, never a default) and
posts through an injectable client with
``post(path, json=, headers=)``, such as ``httpx.AsyncClient``. Non-2xx answers
raise. It relates to ``schedule.py``.
"""

from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from libs.common.internal_auth import service_token_header

ML_SERVICE = "ml"
PATHS = {
    "profiles": "/internal/pulse/ingest/profiles",
    "communities": "/internal/pulse/ingest/communities",
    "topics": "/internal/pulse/ingest/topics",
    "attribution": "/internal/pulse/ingest/attribution",
}


class ProfileResult(BaseModel):
    """Archetype assignment per company."""

    run_id: str
    algorithm: str = "k-prototypes"
    assignments: dict[str, int]


class CommunityResult(BaseModel):
    """Community id and degree centrality per node."""

    run_id: str
    algorithm: str = "leiden"
    communities: dict[str, int]
    centrality: dict[str, float]


class TopicResult(BaseModel):
    """Top terms for a period, plus optional LDA topics."""

    run_id: str
    period: str
    top_terms: list[list[tuple[str, float]]] = Field(default_factory=list)
    lda_topics: list[list[str]] = Field(default_factory=list)


class HttpClient(Protocol):
    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> Any: ...


class PulsePublisher:
    """Post result models to pulse with a signed service token."""

    def __init__(
        self, client: HttpClient, service_secret: str, tenant_id: UUID | str | None = None
    ) -> None:
        self._client = client
        self._secret = service_secret
        self._tenant_id = tenant_id

    async def _post(self, kind: str, body: BaseModel) -> None:
        if not self._tenant_id:
            raise ValueError("tenant_id is required to publish to pulse")
        headers = service_token_header(ML_SERVICE, self._secret)
        headers["X-Tenant-Id"] = str(self._tenant_id)
        response = await self._client.post(PATHS[kind], json=body.model_dump(), headers=headers)
        response.raise_for_status()

    async def publish_profiles(self, result: ProfileResult) -> None:
        await self._post("profiles", result)

    async def publish_communities(self, result: CommunityResult) -> None:
        await self._post("communities", result)

    async def publish_topics(self, result: TopicResult) -> None:
        await self._post("topics", result)
