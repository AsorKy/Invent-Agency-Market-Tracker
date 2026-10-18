"""Unit tests for CSV imports as background jobs (REQ-ING-005, REQ-QA-002).

An in-memory transaction serves the job tables and the company, lead, and activity writers.
Proves limits (10 MB, 100,000 rows, 3 active jobs with 429), idempotent submission, chunked
processing with row hashes, per-row error isolation, outbox events, progress counters, blob
purge, and the HTTP flow (202 and job status). Neither a database nor Polars is needed.
"""

import asyncio
import csv
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("pydantic")
from services.core.app import imports  # noqa: E402
from services.core.app.imports import (  # noqa: E402
    ImportEntity,
    ImportRejectedError,
    JobNotFoundError,
    create_import,
    parse_chunks,
    process_next_job,
    read_job,
    row_sha256,
)
from services.core.app.leads import IdempotencyKeyReuseError  # noqa: E402

TENANT = uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_csv(rows: list[dict[str, str]], columns: list[str] | None = None) -> bytes:
    columns = columns or list(rows[0])
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


class Db:
    def __init__(self) -> None:
        self.jobs: dict[UUID, dict[str, Any]] = {}
        self.blobs: dict[UUID, bytes] = {}
        self.rows: list[dict[str, Any]] = []
        self.companies: dict[UUID, dict[str, Any]] = {}
        self.leads: dict[UUID, dict[str, Any]] = {}
        self.idem: dict[tuple[UUID, str], dict[str, Any]] = {}
        self.activities: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.commits = 0

    def state(self) -> Any:
        return (
            self.jobs,
            self.blobs,
            self.rows,
            self.companies,
            self.leads,
            self.idem,
            self.activities,
            self.outbox,
        )

    def restore(self, snap: Any) -> None:
        (
            self.jobs,
            self.blobs,
            self.rows,
            self.companies,
            self.leads,
            self.idem,
            self.activities,
            self.outbox,
        ) = snap

    def uow(self) -> "Uow":
        return Uow(self)


class Uow:
    def __init__(self, db: Db) -> None:
        self.db = db

    async def __aenter__(self) -> "Uow":
        self.snap = deepcopy(self.db.state())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.db.commits += 1
        else:
            self.db.restore(self.snap)

    # The same object serves as company, lead, and action transaction.
    @property
    def company_tx(self) -> "Uow":
        return self

    lead_tx = company_tx
    action_tx = company_tx

    @asynccontextmanager
    async def row_scope(self) -> AsyncIterator[None]:
        snap = deepcopy(self.db.state())
        try:
            yield
        except BaseException:
            self.db.restore(snap)
            raise

    async def lock_key(self, key: str) -> None:
        pass

    # --- job tables
    async def find_job_by_key(self, tenant_id: UUID, key: str) -> dict[str, Any] | None:
        for job in self.db.jobs.values():
            if job["tenant_id"] == tenant_id and job["idempotency_key"] == key:
                return dict(job)
        return None

    async def count_active_jobs(self, tenant_id: UUID) -> int:
        return sum(
            1
            for j in self.db.jobs.values()
            if j["tenant_id"] == tenant_id and j["status"] in ("queued", "running")
        )

    async def insert_upload_and_job(self, row: dict[str, Any]) -> None:
        self.db.blobs[row["upload_id"]] = row["content"]
        job = {k: v for k, v in row.items() if k != "content"}
        self.db.jobs[row["job_id"]] = job | {
            "status": "queued",
            "processed_rows": 0,
            "upserted_rows": 0,
            "duplicate_rows": 0,
            "error_rows": 0,
        }

    async def get_job(self, tenant_id: UUID | None, job_id: UUID) -> dict[str, Any] | None:
        job = self.db.jobs.get(job_id)
        if job is None or (tenant_id and job["tenant_id"] != tenant_id):
            return None
        return dict(job)

    async def claim_next_job(self, now: datetime) -> dict[str, Any] | None:
        for job in self.db.jobs.values():
            if job["status"] == "queued":
                job.update(status="running", started_at=now)
                return dict(job) | {"content": self.db.blobs[job["upload_id"]]}
        return None

    async def insert_row(
        self, job: dict[str, Any], row_number: int, sha: str, payload: dict[str, Any]
    ) -> bool:
        if any(
            r["tenant_id"] == job["tenant_id"] and r["entity"] == job["entity"] and r["sha"] == sha
            for r in self.db.rows
        ):
            return False
        self.db.rows.append(
            {
                "tenant_id": job["tenant_id"],
                "job_id": job["job_id"],
                "entity": job["entity"],
                "n": row_number,
                "sha": sha,
                "status": "pending",
            }
        )
        return True

    async def set_row_status(self, job_id: UUID, row_number: int, status: str) -> None:
        for r in self.db.rows:
            if r["job_id"] == job_id and r["n"] == row_number:
                r["status"] = status

    async def record_error_row(
        self, job: dict[str, Any], row_number: int, sha: str, payload: Any, error: str
    ) -> None:
        self.db.rows.append(
            {
                "tenant_id": job["tenant_id"],
                "job_id": job["job_id"],
                "entity": job["entity"],
                "n": row_number,
                "sha": sha + "-err",
                "status": "error",
                "error": error,
            }
        )

    async def add_progress(self, job_id: UUID, counters: dict[str, int]) -> None:
        job = self.db.jobs[job_id]
        job["processed_rows"] += counters["processed"]
        job["upserted_rows"] += counters["upserted"]
        job["duplicate_rows"] += counters["duplicates"]
        job["error_rows"] += counters["errors"]

    async def finish_job(
        self, job: dict[str, Any], *, status: str, error: str | None, now: datetime
    ) -> None:
        self.db.jobs[job["job_id"]].update(status=status, error_message=error, finished_at=now)
        if not job["preserve_blob"]:
            self.db.blobs[job["upload_id"]] = b""

    # --- company writer
    async def find_company(self, nit: str | None, slug: str) -> dict[str, Any] | None:
        for row in self.db.companies.values():
            if row["slug"] == slug:
                return deepcopy(row)
        return None

    async def insert_company(self, row: dict[str, Any]) -> None:
        self.db.companies[row["id"]] = deepcopy(row)

    async def close_version(self, old: dict[str, Any], closed_at: Any) -> None:
        pass

    async def update_company(self, row: dict[str, Any]) -> None:
        self.db.companies[row["id"]] = deepcopy(row)

    async def add_outbox_event(self, **event: Any) -> None:
        self.db.outbox.append(event)

    # --- lead writer
    async def get_idempotency(self, tenant_id: UUID, key: str) -> dict[str, Any] | None:
        return self.db.idem.get((tenant_id, key))

    async def save_idempotency(self, *, tenant_id: UUID, key: str, **kw: Any) -> None:
        self.db.idem[(tenant_id, key)] = {
            "request_hash": kw["request_hash"],
            "response_body": kw["response"],
        }

    async def insert_lead(self, row: dict[str, Any]) -> None:
        self.db.leads[row["id"]] = dict(row)

    # --- activity writer
    async def get_lead(self, lead_id: UUID, tenant_id: UUID | None) -> dict[str, Any] | None:
        row = self.db.leads.get(lead_id)
        return dict(row) if row else None

    async def find_activity(self, tenant_id: UUID, key: str) -> dict[str, Any] | None:
        for a in self.db.activities:
            if a["idempotency_key"] == key:
                return a
        return None

    async def insert_activity(self, row: dict[str, Any]) -> None:
        self.db.activities.append(dict(row))


def _submit(db: Db, content: bytes, key: str = "k", entity: str = "companies", **kw: Any) -> Any:
    return asyncio.run(
        create_import(
            db.uow(),
            ImportEntity(entity),
            content,
            tenant_id=kw.pop("tenant_id", TENANT),
            idempotency_key=key,
            **kw,
        )
    )


def _work(db: Db) -> str | None:
    return asyncio.run(process_next_job(db.uow, now=lambda: NOW))


COMPANIES = [
    {"legal_name": "Acme SAS", "nit": "900.1-1", "city": "Cali", "employee_count": "10"},
    {"legal_name": "Beta SAS", "nit": "900.2-2", "city": "Bogota", "employee_count": ""},
]


def test_parse_chunks_splits_and_numbers_rows() -> None:
    content = make_csv([{"a": str(i)} for i in range(7)])
    chunks = list(parse_chunks(content, 3))
    assert [len(c) for c in chunks] == [3, 3, 1]
    assert chunks[0][0] == (1, {"a": "0"}) and chunks[2][0][0] == 7
    assert list(parse_chunks(b"\xef\xbb\xbfa\n1\n", 5))[0][0][1] == {"a": "1"}
    assert imports.read_header(b"\xef\xbb\xbfa, b\n1,2\n") == ["a", "b"]


def test_row_hash_ignores_blanks_and_order() -> None:
    a = row_sha256(ImportEntity.COMPANIES, {"x": "1", "y": " "})
    assert a == row_sha256(ImportEntity.COMPANIES, {"y": "", "x": "1"})
    assert a != row_sha256(ImportEntity.LEADS, {"x": "1", "y": ""})


def test_submit_queues_job_with_blob_in_one_transaction() -> None:
    db = Db()
    view, created = _submit(db, make_csv(COMPANIES))
    assert created and view["status"] == "queued" and view["progress"]["total"] == 2
    (job,) = db.jobs.values()
    assert db.blobs[job["upload_id"]] and db.commits == 1


def test_idempotent_submit_and_key_reuse() -> None:
    db = Db()
    content = make_csv(COMPANIES)
    first, _ = _submit(db, content)
    again, created = _submit(db, content)
    assert not created and again["job_id"] == first["job_id"] and len(db.jobs) == 1
    with pytest.raises(IdempotencyKeyReuseError):
        _submit(db, make_csv(COMPANIES[:1]))


def test_limits_size_rows_header_and_active_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Db()
    with pytest.raises(ImportRejectedError) as big:
        _submit(db, b"a" * (imports.MAX_BYTES + 1))
    assert (big.value.code, big.value.status_code) == ("file_too_large", 413)
    monkeypatch.setattr(imports, "MAX_ROWS", 2)
    with pytest.raises(ImportRejectedError) as rows:
        _submit(db, make_csv([{"legal_name": str(i)} for i in range(3)]))
    assert rows.value.code == "too_many_rows"
    monkeypatch.setattr(imports, "MAX_ROWS", 100_000)
    with pytest.raises(ImportRejectedError) as header:
        _submit(db, make_csv([{"nope": "1"}]))
    assert header.value.code == "invalid_header"
    with pytest.raises(ImportRejectedError) as empty:
        _submit(db, b"  \n")
    assert empty.value.code == "empty_file"
    assert db.jobs == {}
    for i in range(3):
        _submit(db, make_csv(COMPANIES), key=f"k{i}")
    with pytest.raises(ImportRejectedError) as busy:
        _submit(db, make_csv(COMPANIES), key="k9")
    assert (busy.value.code, busy.value.status_code) == ("too_many_active_jobs", 429)
    _submit(db, make_csv(COMPANIES), key="other", tenant_id=uuid4())  # other tenant is unaffected


def test_company_job_upserts_emits_events_and_purges_blob() -> None:
    db = Db()
    view, _ = _submit(db, make_csv(COMPANIES))
    assert _work(db) == "succeeded"
    done = asyncio.run(read_job(db.uow(), UUID(view["job_id"]), TENANT))
    assert done["status"] == "succeeded" and done["finished_at"] == NOW.isoformat()
    assert done["progress"] == {
        "total": 2,
        "processed": 2,
        "upserted": 2,
        "duplicates": 0,
        "errors": 0,
    }
    assert len(db.companies) == 2 and len(db.outbox) == 2
    assert all(c["tenant_id"] == TENANT for c in db.companies.values())
    assert [r["status"] for r in db.rows] == ["upserted", "upserted"]
    assert set(db.blobs.values()) == {b""}
    assert _work(db) is None


def test_preserve_blob_keeps_content() -> None:
    db = Db()
    _submit(db, make_csv(COMPANIES), preserve_blob=True)
    _work(db)
    assert all(db.blobs.values())


def test_reimport_counts_duplicates_without_new_events() -> None:
    db = Db()
    _submit(db, make_csv(COMPANIES), key="a")
    _work(db)
    view, _ = _submit(db, make_csv(COMPANIES + [COMPANIES[0]]), key="b")
    _work(db)
    job = db.jobs[UUID(view["job_id"])]
    assert (job["upserted_rows"], job["duplicate_rows"]) == (0, 3)
    assert len(db.outbox) == 2


def test_bad_rows_are_isolated_and_counted() -> None:
    db = Db()
    rows = [
        {"legal_name": "Good SAS", "employee_count": "5"},
        {"legal_name": "", "employee_count": ""},
        {"legal_name": "Neg SAS", "employee_count": "-4"},
        {"legal_name": "Fine SAS", "employee_count": ""},
    ]
    view, _ = _submit(db, make_csv(rows))
    assert _work(db) == "succeeded"
    job = db.jobs[UUID(view["job_id"])]
    assert (job["upserted_rows"], job["error_rows"], job["processed_rows"]) == (2, 2, 4)
    errors = [r for r in db.rows if r["status"] == "error"]
    assert {r["n"] for r in errors} == {2, 3} and "legal_name" in errors[0]["error"]
    assert len(db.companies) == 2


def test_writer_failure_rolls_back_only_that_row(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Db()
    _submit(db, make_csv(COMPANIES))
    original = Uow.insert_company

    async def flaky(self: Uow, row: dict[str, Any]) -> None:
        if row["legal_name"] == "Acme SAS":
            raise RuntimeError("db hiccup")
        await original(self, row)

    monkeypatch.setattr(Uow, "insert_company", flaky)
    _work(db)
    job = next(iter(db.jobs.values()))
    assert (job["upserted_rows"], job["error_rows"]) == (1, 1)
    assert [r["status"] for r in db.rows if r["n"] == 1] == ["error"]  # insert_row was rolled back


def test_chunks_commit_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Db()
    monkeypatch.setattr(imports, "CHUNK_ROWS", 1)
    _submit(db, make_csv(COMPANIES))
    before = db.commits
    _work(db)
    assert db.commits - before == 1 + 2 + 1  # claim, two chunks, finish


def test_parser_failure_fails_job_and_purges_blob() -> None:
    db = Db()
    view, _ = _submit(db, make_csv(COMPANIES))

    def broken(content: bytes, size: int) -> Any:
        raise OSError("parser exploded")
        yield  # pragma: no cover

    assert asyncio.run(process_next_job(db.uow, parser=broken, now=lambda: NOW)) == "failed"
    job = db.jobs[UUID(view["job_id"])]
    assert job["status"] == "failed" and "parser exploded" in job["error_message"]
    assert set(db.blobs.values()) == {b""}


def test_leads_and_activities_import() -> None:
    db = Db()
    company_id = uuid4()
    _submit(db, make_csv([{"company_id": str(company_id), "service_line": "saas"}]), "l", "leads")
    assert _work(db) == "succeeded"
    (lead_id,) = db.leads
    _submit(
        db,
        make_csv(
            [
                {"lead_id": str(lead_id), "activity_type": "call", "channel": "phone"},
                {"lead_id": str(lead_id), "activity_type": "fax", "channel": ""},
                {"lead_id": str(uuid4()), "activity_type": "note", "channel": ""},
            ]
        ),
        "a",
        "activities",
    )
    _work(db)
    activities_job = next(j for j in db.jobs.values() if j["entity"] == "activities")
    assert (activities_job["upserted_rows"], activities_job["error_rows"]) == (1, 2)
    assert len(db.activities) == 1
    assert {e["event_type"] for e in db.outbox if "event_type" in e} == {"signal.activity_logged"}
    assert len(db.outbox) == 2  # lead.created and the activity event
    assert next(j for j in db.jobs.values() if j["entity"] == "leads")["upserted_rows"] == 1


def test_read_job_unknown_or_other_tenant() -> None:
    db = Db()
    view, _ = _submit(db, make_csv(COMPANIES))
    with pytest.raises(JobNotFoundError):
        asyncio.run(read_job(db.uow(), uuid4(), None))
    with pytest.raises(JobNotFoundError):
        asyncio.run(read_job(db.uow(), UUID(view["job_id"]), uuid4()))


def test_routes_return_202_then_status() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from services.core.app import main

    db = Db()

    class Repo:
        def import_unit_of_work(self) -> Uow:
            return db.uow()

    main.app.state.repository = Repo()
    main.app.dependency_overrides[main.require_internal_token] = lambda: None
    client = TestClient(main.app)
    h = {"X-Tenant-ID": str(TENANT), "Idempotency-Key": "r1", "Content-Type": "text/csv"}
    try:
        url = "/internal/core/imports/companies"
        content = make_csv(COMPANIES)
        accepted = client.post(url, content=content, headers=h)
        assert accepted.status_code == 202
        body = accepted.json()
        assert body["status"] == "queued" and body["status_url"].endswith(body["job_id"])
        assert client.post(url, content=content, headers=h).status_code == 200
        assert (
            client.post(
                url, content=b"nope\n1\n", headers=h | {"Idempotency-Key": "r2"}
            ).status_code
            == 422
        )
        assert (
            client.post(url, content=content, headers={"X-Tenant-ID": str(TENANT)}).status_code
            == 422
        )
        assert (
            client.post("/internal/core/imports/people", content=content, headers=h).status_code
            == 422
        )
        big = client.post(
            url,
            content=content,
            headers=h | {"Idempotency-Key": "r3", "Content-Length": str(imports.MAX_BYTES + 1)},
        )
        assert big.status_code in (413, 400)
        _work(db)
        job = client.get(f"/internal/core/jobs/{body['job_id']}")
        assert job.status_code == 200 and job.json()["progress"]["upserted"] == 2
        assert client.get(f"/internal/core/jobs/{uuid4()}").status_code == 404
        for i in range(3):
            client.post(url, content=content, headers=h | {"Idempotency-Key": f"q{i}"})
        busy = client.post(url, content=content, headers=h | {"Idempotency-Key": "q9"})
        assert busy.status_code == 429 and busy.json()["detail"] == "too_many_active_jobs"
        same = client.post(
            url, content=make_csv(COMPANIES[:1]), headers=h | {"Idempotency-Key": "r1"}
        )
        assert same.status_code == 422
    finally:
        main.app.dependency_overrides.clear()
