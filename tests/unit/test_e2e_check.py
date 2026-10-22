"""The e2e data check passes, waits, and fails on scripted row counts (no Docker needed).

It relates to ``ops/e2e_check.py`` and the e2e job in ``.github/workflows/ci.yml``.
"""

from collections.abc import Callable

import httpx
import pytest

from ops import e2e_check


def scripted(counts: dict[str, int]) -> Callable[[str], int]:
    """A query function answering from a table name or one of the outbox statements."""

    def query(sql: str) -> int:
        if sql == e2e_check.PENDING_OUTBOX:
            return counts.get("pending", 0)
        if sql == e2e_check.DEAD_LETTERS:
            return counts.get("dead", 0)
        if sql == e2e_check.COMPANIES_UNSCORED:
            return counts.get("unscored", 0)
        if sql == e2e_check.SCORES_WITHOUT_FEATURES:
            return counts.get("no_features", 0)
        if sql == e2e_check.SCORES_WITH_FEATURES:
            return counts.get("with_features", counts.get("ml.lead_score", 0))
        return counts.get(sql.rsplit(" ", 1)[-1], 0)

    return query


FILLED = {
    "core.company": 60,
    "core.signal": 150,
    "pulse.fact_signal": 150,
    "pulse.daily_rollup": 84,
    "ml.lead_score": 60,
    "brief.record_digest": 60,
}


def test_passes_when_core_is_filled_and_the_outbox_is_drained(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = e2e_check.run(["--timeout", "0"], query=scripted(FILLED), sleep=lambda _s: None)
    assert code == 0
    out = capsys.readouterr().out
    assert "core.company: 60 (required)" in out
    assert "ml.lead_score: 60 (required)" in out
    assert "brief.record_digest: 60 (required)" in out
    assert "core.company without score: 0" in out
    assert "ml.lead_score with features: 60" in out
    assert "core.brief: 0 (info)" in out
    assert "ingest.raw_snapshot: 0 (info)" in out
    assert "e2e data check: OK" in out


def test_fails_when_a_required_table_is_empty(capsys: pytest.CaptureFixture[str]) -> None:
    counts = {**FILLED, "core.signal": 0}
    assert e2e_check.run(["--timeout", "0"], query=scripted(counts), sleep=lambda _s: None) == 1
    assert "FAIL: core.signal is empty" in capsys.readouterr().err


@pytest.mark.parametrize(
    "table", ["pulse.fact_signal", "pulse.daily_rollup", "ml.lead_score", "brief.record_digest"]
)
def test_fails_when_a_downstream_table_is_empty(
    table: str, capsys: pytest.CaptureFixture[str]
) -> None:
    counts = {**FILLED, table: 0}
    assert e2e_check.run(["--timeout", "0"], query=scripted(counts), sleep=lambda _s: None) == 1
    assert f"FAIL: {table} is empty" in capsys.readouterr().err


def test_fails_when_companies_have_no_score(capsys: pytest.CaptureFixture[str]) -> None:
    counts = {**FILLED, "unscored": 4}
    assert e2e_check.run(["--timeout", "0"], query=scripted(counts), sleep=lambda _s: None) == 1
    assert "FAIL: 4 core.company rows have no score" in capsys.readouterr().err


def test_fails_when_scores_lack_stored_features(capsys: pytest.CaptureFixture[str]) -> None:
    counts = {**FILLED, "no_features": 2, "with_features": 58}
    assert e2e_check.run(["--timeout", "0"], query=scripted(counts), sleep=lambda _s: None) == 1
    assert "FAIL: 2 ml.lead_score rows have no stored features" in capsys.readouterr().err


def test_fails_when_no_score_has_features(capsys: pytest.CaptureFixture[str]) -> None:
    counts = {**FILLED, "with_features": 0}
    assert e2e_check.run(["--timeout", "0"], query=scripted(counts), sleep=lambda _s: None) == 1
    assert "FAIL: no ml.lead_score row has stored features" in capsys.readouterr().err


def test_score_checks_read_the_columns_the_migrations_add() -> None:
    assert "score IS NULL" in e2e_check.COMPANIES_UNSCORED
    assert "FROM core.company" in e2e_check.COMPANIES_UNSCORED
    for column in ("feature_icp_fit", "feature_signal_strength", "feature_territory_opportunity"):
        assert column in e2e_check.SCORES_WITHOUT_FEATURES
        assert column in e2e_check.SCORES_WITH_FEATURES


def test_fails_on_pending_events_and_dead_letters(capsys: pytest.CaptureFixture[str]) -> None:
    counts = {**FILLED, "pending": 3, "dead": 1}
    code = e2e_check.run(
        ["--timeout", "0"],
        query=scripted(counts),
        sleep=lambda _s: None,
        errors=lambda: ["missing_tenant_id x1"],
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "3 outbox events are still unpublished" in captured.err
    assert "1 events went to the dead letter table" in captured.err
    assert "dead letter error: missing_tenant_id x1" in captured.out


def test_strict_requires_every_table() -> None:
    args = ["--timeout", "0", "--strict"]
    assert e2e_check.run(args, query=scripted(FILLED), sleep=lambda _s: None) == 1


def test_waits_until_the_projections_fill() -> None:
    state = {"polls": 0}

    def query(sql: str) -> int:
        if sql == e2e_check.PENDING_OUTBOX:
            return 2 if state["polls"] < 2 else 0
        if sql in (
            e2e_check.DEAD_LETTERS,
            e2e_check.COMPANIES_UNSCORED,
            e2e_check.SCORES_WITHOUT_FEATURES,
        ):
            return 0
        return 5

    def sleep(_seconds: float) -> None:
        state["polls"] += 1

    assert e2e_check.run(["--timeout", "60", "--interval", "0"], query=query, sleep=sleep) == 0
    assert state["polls"] == 2


def test_psql_failure_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "relation does not exist"

    monkeypatch.setattr(e2e_check.subprocess, "run", lambda *_a, **_k: Failed())
    with pytest.raises(RuntimeError, match="relation does not exist"):
        e2e_check.compose_query("SELECT 1")


# T-164: the --chain mode.


def chain_query(published: int, unpublished: int = 0, dead: int = 0) -> Callable[[str], int]:
    base = scripted({**FILLED, "dead": dead})

    def query(sql: str) -> int:
        if sql == e2e_check.CHAIN_PUBLISHED:
            return published
        if sql == e2e_check.CHAIN_UNPUBLISHED:
            return unpublished
        return base(sql)

    return query


def test_chain_mode_drives_then_passes_when_every_type_is_published(
    capsys: pytest.CaptureFixture[str],
) -> None:
    driven: list[str] = []
    code = e2e_check.run(
        ["--timeout", "0", "--chain"],
        query=chain_query(len(e2e_check.CHAIN_TYPES)),
        sleep=lambda _s: None,
        drive=lambda: driven.append("x"),
    )
    assert code == 0
    assert driven == ["x"]
    assert "e2e chain check: OK" in capsys.readouterr().out


def test_chain_mode_is_off_without_the_flag() -> None:
    def drive() -> None:
        raise AssertionError("the driver must not run without --chain")

    assert e2e_check.run(["--timeout", "0"], query=scripted(FILLED), drive=drive) == 0


def test_chain_mode_does_not_drive_when_the_normal_checks_fail() -> None:
    def drive() -> None:
        raise AssertionError("no driver after a failed normal check")

    counts = {**FILLED, "core.signal": 0}
    code = e2e_check.run(["--timeout", "0", "--chain"], query=scripted(counts), drive=drive)
    assert code == 1


def test_chain_mode_fails_on_missing_types_and_unpublished_events(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = e2e_check.run(
        ["--timeout", "0", "--chain"],
        query=chain_query(3, unpublished=2),
        sleep=lambda _s: None,
        drive=lambda: None,
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "only 3 of 5 chain event types are published" in err
    assert "2 chain events are still unpublished" in err


def test_chain_failures_include_dead_letters() -> None:
    assert e2e_check.chain_failures(5, 0, 0) == []
    assert e2e_check.chain_failures(5, 0, 1) == ["1 events went to the dead letter table"]


def test_chain_mode_reports_a_failed_driver(capsys: pytest.CaptureFixture[str]) -> None:
    def drive() -> None:
        raise RuntimeError("signal create answered 403")

    code = e2e_check.run(
        ["--timeout", "0", "--chain"], query=chain_query(5), sleep=lambda _s: None, drive=drive
    )
    assert code == 1
    assert "FAIL: signal create answered 403" in capsys.readouterr().err


def test_chain_mode_waits_for_the_relay() -> None:
    state = {"polls": 0}
    base = chain_query(5)

    def query(sql: str) -> int:
        if sql == e2e_check.CHAIN_PUBLISHED:
            return 5 if state["polls"] >= 2 else 1
        return base(sql)

    def sleep(_seconds: float) -> None:
        state["polls"] += 1

    code = e2e_check.run(
        ["--timeout", "60", "--interval", "0", "--chain"],
        query=query,
        sleep=sleep,
        drive=lambda: None,
    )
    assert code == 0
    assert state["polls"] == 2


def test_chain_queries_name_the_five_outbox_types() -> None:
    assert e2e_check.CHAIN_TYPES == (
        "signal.tagged",
        "company.matched",
        "signal.explained",
        "project.pipeline_updated",
        "project.forecast_updated",
    )
    for name in e2e_check.CHAIN_TYPES:
        assert f"'{name}'" in e2e_check.CHAIN_PUBLISHED
        assert f"'{name}'" in e2e_check.CHAIN_UNPUBLISHED
    assert "published_at IS NOT NULL" in e2e_check.CHAIN_PUBLISHED
    assert "published_at IS NULL" in e2e_check.CHAIN_UNPUBLISHED


def _core_transport(
    seen: list[httpx.Request], *, signal_status: int = 201, status_status: int = 200
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/internal/core/companies":
            return httpx.Response(200, json={"data": [{"id": "c-1"}]})
        if path == "/internal/core/signals":
            return httpx.Response(signal_status, json={"signal_id": "s-1"})
        if path == "/internal/core/leads":
            return httpx.Response(201, json={"lead_id": "l-1", "stage": "discovered", "version": 1})
        return httpx.Response(status_status, json={})

    return httpx.MockTransport(handler)


def test_driver_creates_one_signal_and_moves_one_lead() -> None:
    import asyncio

    seen: list[httpx.Request] = []

    async def go() -> dict[str, str]:
        async with httpx.AsyncClient(
            base_url="http://core", transport=_core_transport(seen)
        ) as client:
            return await e2e_check.drive_chain(client, "secret")

    assert asyncio.run(go()) == {"company_id": "c-1", "signal_id": "s-1"}
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/internal/core/companies"),
        ("POST", "/internal/core/signals"),
        ("POST", "/internal/core/leads"),
        ("PATCH", "/internal/core/leads/l-1/status"),
    ]
    assert all(r.headers["x-service-token"] for r in seen)
    assert seen[2].headers["x-tenant-id"] and seen[2].headers["idempotency-key"]
    assert b'"expected_version":1' in seen[3].content.replace(b" ", b"")


def test_driver_raises_when_core_refuses_a_step() -> None:
    import asyncio

    async def go() -> None:
        async with httpx.AsyncClient(
            base_url="http://core", transport=_core_transport([], signal_status=403)
        ) as client:
            await e2e_check.drive_chain(client, "secret")

    with pytest.raises(RuntimeError, match="signal create answered 403"):
        asyncio.run(go())
