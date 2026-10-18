"""Unit tests for raw snapshot ingestion and change detection (REQ-ING-001).

Covers the canonical sha256, record-level change detection, idempotent storage
keyed by (source, snapshot id, sha256), one processor job per snapshot, and the
fast 202 webhook answer. ``IngestRepository`` runs against an in-memory fake
engine and fake Redis, so no Postgres or Redis is needed.
"""

import json
from typing import Any

import pytest

pytest.importorskip("orjson")
pytest.importorskip("sqlalchemy")
pytest.importorskip("redis")
pytest.importorskip("pydantic_settings")

from services.ingest.app.repository import IngestRepository  # noqa: E402
from services.ingest.app.snapshot import (  # noqa: E402
    changed_records,
    payload_sha256,
    record_identity,
)

A = {"place_id": "p1", "name": "Cafe Uno", "rating": 4.5}
B = {"place_id": "p2", "name": "Cafe Dos", "rating": 4.0}


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.rowcount = len(rows)

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalar_one(self) -> Any:
        return self.rows[0]

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> Any:
        return self.rows[0] if self.rows else None


class FakeConnection:
    """Emulate the few statements the repository issues against raw_snapshot."""

    def __init__(self, store: dict[tuple[str, str, str], dict[str, Any]]) -> None:
        self.store = store

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        sql = str(statement)
        params = params or {}
        if "INSERT INTO ingest.raw_snapshot" in sql:
            key = ("bright_data", params["snapshot_id"], params["payload_sha256"])
            if key in self.store:
                return FakeResult([])
            self.store[key] = {"id": f"row-{len(self.store) + 1}", **params}
            return FakeResult([self.store[key]["id"]])
        if "SELECT id FROM ingest.raw_snapshot" in sql:
            key = ("bright_data", params["snapshot_id"], params["payload_sha256"])
            return FakeResult([self.store[key]["id"]])
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeEngine:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str, str], dict[str, Any]] = {}

    def begin(self) -> "FakeEngine":
        return self

    async def __aenter__(self) -> FakeConnection:
        return FakeConnection(self.store)

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class FakeRedis:
    def __init__(self) -> None:
        self.marks: set[str] = set()
        self.stream: list[dict[str, str]] = []

    async def set(self, key: str, _value: str, *, nx: bool, ex: int) -> bool | None:
        if nx and key in self.marks:
            return None
        self.marks.add(key)
        return True

    async def xadd(self, _stream: str, fields: dict[str, str]) -> str:
        self.stream.append(fields)
        return "1-0"

    async def delete(self, key: str) -> None:
        self.marks.discard(key)


def make_repository() -> tuple[IngestRepository, FakeEngine, FakeRedis]:
    repository = IngestRepository.__new__(IngestRepository)
    engine, redis = FakeEngine(), FakeRedis()
    repository.engine = engine  # type: ignore[assignment]
    repository.redis = redis  # type: ignore[assignment]
    repository.enqueue_mark_ttl_seconds = 60
    return repository, engine, redis


async def persist(repository: IngestRepository, snapshot_id: str, payload: Any) -> Any:
    from datetime import UTC, datetime

    return await repository.persist_snapshot(
        snapshot_id=snapshot_id,
        dataset_id="d-1",
        payload=payload,
        payload_sha256=payload_sha256(payload),
        fetched_at=datetime.now(UTC),
    )


def test_hash_ignores_key_order_and_detects_changes() -> None:
    assert payload_sha256({"a": 1, "b": 2}) == payload_sha256({"b": 2, "a": 1})
    assert payload_sha256({"a": 1}) != payload_sha256({"a": 2})
    assert len(payload_sha256([A, B])) == 64


def test_changed_records_without_previous_returns_all() -> None:
    assert changed_records(None, [A, B]) == [A, B]


def test_changed_records_skips_unchanged_and_keeps_new_or_modified() -> None:
    modified = {**B, "rating": 3.0}
    new = {"place_id": "p3", "name": "Cafe Tres"}
    assert changed_records([A, B], [A, modified, new]) == [modified, new]
    assert changed_records([A, B], [B, A]) == []


def test_record_identity_falls_back_to_name_and_city() -> None:
    first = record_identity({"name": " Panaderia  X ", "city": "Cali"})
    assert first == record_identity({"name": "panaderia  x", "city": "Cali"})
    assert first != record_identity({"name": "panaderia  x", "city": "Bogota"})


async def test_new_snapshot_is_stored_and_one_job_is_enqueued() -> None:
    repository, engine, redis = make_repository()
    _row, created, queued = await persist(repository, "s-1", [A, B])
    assert created and queued
    assert len(engine.store) == 1
    assert len(redis.stream) == 1
    assert redis.stream[0]["snapshot_id"] == "s-1"


async def test_replayed_snapshot_is_stored_once_and_not_requeued() -> None:
    repository, engine, redis = make_repository()
    first = await persist(repository, "s-1", [A, B])
    second = await persist(repository, "s-1", [B, A][::-1])
    assert first[0] == second[0]
    assert second[1] is False and second[2] is False
    assert len(engine.store) == 1
    assert len(redis.stream) == 1


async def test_changed_content_is_a_new_row_with_its_own_job() -> None:
    repository, engine, redis = make_repository()
    await persist(repository, "s-1", [A])
    _row, created, queued = await persist(repository, "s-1", [{**A, "rating": 1.0}])
    assert created and queued
    assert len(engine.store) == 2
    assert len(redis.stream) == 2
    stored = [json.loads(v["payload"]) for v in engine.store.values()]
    assert stored[0] != stored[1]


async def test_webhook_answers_202_with_processor_queued(
    monkeypatch: pytest.MonkeyPatch, asgi_client: Any
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("structlog")
    from services.ingest.app import main as ingest

    class Recorder:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def close(self) -> None:
            return None

        async def persist_snapshot(self, **kwargs: Any) -> tuple[str, bool, bool]:
            assert kwargs["payload_sha256"] == payload_sha256(kwargs["payload"])
            return "row-1", True, True

    monkeypatch.setattr(ingest.settings, "bright_data_webhook_secret", None)
    monkeypatch.setattr(ingest, "IngestRepository", Recorder)
    body = {"snapshot_id": "s-1", "dataset_id": "d-1", "payload": [A]}
    async with asgi_client(ingest.app) as client:
        response = await client.post("/internal/ingest/webhook/bd", json=body)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["next_step"] == "processor_queued"
    assert data["payload_sha256"] == payload_sha256([A])
