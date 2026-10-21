"""The e2e data check passes, waits, and fails on scripted row counts (no Docker needed).

It relates to ``ops/e2e_check.py`` and the e2e job in ``.github/workflows/ci.yml``.
"""

from collections.abc import Callable

import pytest

from ops import e2e_check


def scripted(counts: dict[str, int]) -> Callable[[str], int]:
    """A query function answering from a table name or one of the outbox statements."""

    def query(sql: str) -> int:
        if sql == e2e_check.PENDING_OUTBOX:
            return counts.get("pending", 0)
        if sql == e2e_check.DEAD_LETTERS:
            return counts.get("dead", 0)
        return counts.get(sql.rsplit(" ", 1)[-1], 0)

    return query


FILLED = {"core.company": 60, "core.signal": 900, "pulse.fact_signal": 900}


def test_passes_when_core_is_filled_and_the_outbox_is_drained(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = e2e_check.run(["--timeout", "0"], query=scripted(FILLED), sleep=lambda _s: None)
    assert code == 0
    out = capsys.readouterr().out
    assert "core.company: 60 (required)" in out
    assert "ml.lead_score: 0 (info)" in out
    assert "e2e data check: OK" in out


def test_fails_when_a_required_table_is_empty(capsys: pytest.CaptureFixture[str]) -> None:
    counts = {**FILLED, "core.signal": 0}
    assert e2e_check.run(["--timeout", "0"], query=scripted(counts), sleep=lambda _s: None) == 1
    assert "FAIL: core.signal is empty" in capsys.readouterr().err


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
        if sql == e2e_check.DEAD_LETTERS:
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
