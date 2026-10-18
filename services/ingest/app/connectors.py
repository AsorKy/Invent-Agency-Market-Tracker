"""Define the ``SourceConnector`` interface and the mock connector.

A connector triggers a provider collection, fetches the resulting snapshot,
and normalizes one record through a YAML mapping (REQ-ING-004). The mock
connector talks to the Bright Data emulation in ``services/mocks`` through an
injectable ``httpx.Client`` (tests pass a WSGI transport, so no network is
used). The live Bright Data connector is added by T-043 behind the same
interface; ``INGEST_MODE`` selects between them. Methods are synchronous;
async callers wrap them with ``asyncio.to_thread``.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from services.ingest.app.mapping import (
    NormalizedRecord,
    SourceMapping,
    load_mapping,
    normalize_record,
)


class ConnectorError(RuntimeError):
    """Raised when the provider call fails; ``retryable`` marks transient errors."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class TriggerResult:
    """Outcome of a trigger call."""

    snapshot_id: str


@dataclass(frozen=True)
class Progress:
    """Collection progress; ``status`` is running, ready, or failed."""

    snapshot_id: str
    status: str
    records: int = 0


class SourceConnector(ABC):
    """Trigger a collection, fetch its snapshot, normalize its records."""

    def __init__(self, source: str, mapping: SourceMapping | None = None) -> None:
        self.source = source
        self.mapping = mapping or load_mapping(source)

    @abstractmethod
    def trigger(
        self, inputs: list[dict[str, Any]], *, notify_url: str | None = None
    ) -> TriggerResult:
        """Start a collection and return the provider snapshot id."""

    @abstractmethod
    def progress(self, snapshot_id: str) -> Progress:
        """Return the collection status of a snapshot."""

    @abstractmethod
    def fetch(self, snapshot_id: str) -> list[dict[str, Any]]:
        """Download the provider records of a ready snapshot."""

    def normalize(self, record: dict[str, Any]) -> NormalizedRecord:
        """Map one provider record to canonical fields (raises ``MappingError``)."""
        return normalize_record(self.mapping, record)


class MockConnector(SourceConnector):
    """Connector backed by the ``services/mocks`` Bright Data emulation."""

    def __init__(
        self,
        source: str,
        client: httpx.Client,
        *,
        dataset_id: str | None = None,
        mapping: SourceMapping | None = None,
    ) -> None:
        super().__init__(source, mapping)
        self._client = client
        self.dataset_id = dataset_id or f"mock-{source}"

    def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectorError("mock provider unreachable", retryable=True) from exc
        if response.status_code >= 500:
            raise ConnectorError(f"mock provider error {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise ConnectorError(f"mock provider rejected request {response.status_code}")
        return response.json()

    def trigger(
        self, inputs: list[dict[str, Any]], *, notify_url: str | None = None
    ) -> TriggerResult:
        params = {"dataset_id": self.dataset_id}
        if notify_url:
            params["notify"] = notify_url
        body = self._call("POST", "/datasets/v3/trigger", params=params, json=inputs)
        return TriggerResult(snapshot_id=str(body["snapshot_id"]))

    def progress(self, snapshot_id: str) -> Progress:
        body = self._call("GET", f"/datasets/v3/progress/{snapshot_id}")
        return Progress(
            snapshot_id=snapshot_id,
            status=str(body["status"]),
            records=int(body.get("records", 0)),
        )

    def fetch(self, snapshot_id: str) -> list[dict[str, Any]]:
        body = self._call("GET", f"/datasets/v3/snapshot/{snapshot_id}", params={"format": "json"})
        if not isinstance(body, list):
            raise ConnectorError("snapshot is not a JSON array")
        return [record for record in body if isinstance(record, dict)]
