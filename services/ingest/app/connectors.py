"""Define the ``SourceConnector`` interface and the mock connector.

A connector triggers a provider collection, fetches the resulting snapshot,
and normalizes one record through a YAML mapping (REQ-ING-004). The mock
connector talks to the Bright Data emulation in ``services/mocks`` through an
injectable ``httpx.Client`` (tests pass a WSGI transport, so no network is
used). ``BrightDataConnector`` is the live implementation of the Datasets API
(Bearer token, trigger with a notify URL, progress, snapshot download). Only
idempotent calls (progress, download) are retried with exponential backoff; a
trigger is never retried because it spends budget. ``source_config.py``
selects between the two by ``INGEST_MODE``. Methods are synchronous; async
callers wrap them with ``asyncio.to_thread``. Secrets are never logged or put
in error messages.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
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


RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
PROGRESS_STATUS = {
    "starting": "running",
    "running": "running",
    "building": "running",
    "ready": "ready",
    "failed": "failed",
    "canceled": "failed",
    "cancelled": "failed",
}


class BrightDataConnector(SourceConnector):
    """Live connector for the Bright Data Datasets API."""

    def __init__(
        self,
        source: str,
        client: httpx.Client,
        *,
        dataset_id: str,
        api_token: str,
        mapping: SourceMapping | None = None,
        retry_attempts: int = 3,
        retry_base_delay_seconds: float = 0.5,
        retry_max_delay_seconds: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(source, mapping)
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be at least 1")
        self._client = client
        self.dataset_id = dataset_id
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._attempts = retry_attempts
        self._base_delay = retry_base_delay_seconds
        self._max_delay = retry_max_delay_seconds
        self._sleep = sleep

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send one request; map transport errors and bad statuses to ``ConnectorError``."""
        try:
            response = self._client.request(method, url, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectorError("Bright Data unreachable", retryable=True) from exc
        status = response.status_code
        if status in RETRYABLE_STATUS:
            raise ConnectorError(f"Bright Data error {status}", retryable=True)
        if status >= 400:
            raise ConnectorError(f"Bright Data rejected request {status}")
        return response

    def _send_idempotent(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Like ``_send`` but retry transient failures with capped exponential backoff."""
        for attempt in range(self._attempts):
            try:
                return self._send(method, url, **kwargs)
            except ConnectorError as exc:
                if not exc.retryable or attempt == self._attempts - 1:
                    raise
                self._sleep(min(self._max_delay, self._base_delay * 2**attempt))
        raise ConnectorError("retry loop exited unexpectedly")  # pragma: no cover

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError("Bright Data returned invalid JSON") from exc

    def trigger(
        self, inputs: list[dict[str, Any]], *, notify_url: str | None = None
    ) -> TriggerResult:
        params = {"dataset_id": self.dataset_id, "include_errors": "true"}
        if notify_url:
            params["notify"] = notify_url
        body = self._json(self._send("POST", "/datasets/v3/trigger", params=params, json=inputs))
        snapshot_id = body.get("snapshot_id") if isinstance(body, dict) else None
        if not snapshot_id:
            raise ConnectorError("Bright Data trigger response has no snapshot_id")
        return TriggerResult(snapshot_id=str(snapshot_id))

    def progress(self, snapshot_id: str) -> Progress:
        body = self._json(self._send_idempotent("GET", f"/datasets/v3/progress/{snapshot_id}"))
        if not isinstance(body, dict):
            raise ConnectorError("Bright Data progress response is not an object")
        raw = str(body.get("status", "")).lower()
        status = PROGRESS_STATUS.get(raw)
        if status is None:
            raise ConnectorError(f"unknown Bright Data status {raw[:32]!r}")
        return Progress(
            snapshot_id=snapshot_id, status=status, records=int(body.get("records") or 0)
        )

    def fetch(self, snapshot_id: str) -> list[dict[str, Any]]:
        response = self._send_idempotent(
            "GET", f"/datasets/v3/snapshot/{snapshot_id}", params={"format": "json"}
        )
        if response.status_code == 202:
            raise ConnectorError("snapshot is not ready", retryable=True)
        body = self._json(response)
        if not isinstance(body, list):
            raise ConnectorError("snapshot is not a JSON array")
        return [record for record in body if isinstance(record, dict)]
