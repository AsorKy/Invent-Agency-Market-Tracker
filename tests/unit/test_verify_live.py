"""Unit tests for ops/verify_live.py with a fake stack (REQ-RUN-006)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ops import verify_live as vl

SENTINEL = "SENTINEL-s3cr3t-4d5e6f"


def _env(**extra: str) -> dict[str, str]:
    values = {
        "INGEST_MODE": "live",
        "SOURCE_MAPS_ENABLED": "true",
        "SOURCE_MAPS_DATASET_ID": "gd_x",
        "BRIGHT_DATA_API_TOKEN": SENTINEL,
        "INTERNAL_SERVICE_TOKEN": SENTINEL,
    }
    values.update(extra)
    return values


class FakeBackend:
    def __init__(self, missing: str | None = None, trigger_error: bool = False) -> None:
        self.missing = missing
        self.trigger_error = trigger_error
        self.triggered: list[list[dict[str, Any]]] = []

    def trigger(self, source: str, inputs: list[dict[str, Any]]) -> str:
        if self.trigger_error:
            raise vl.StackError("HTTP 502")
        self.triggered.append(inputs)
        return "snap-1"

    def count(self, hop: str, source: str, snapshot_id: str, since: datetime) -> int:
        return 0 if hop == self.missing else 2

    def records(self, snapshot_id: str) -> list[dict[str, Any]]:
        return [{"name": "Acme"}]

    def api_read(self, source: str) -> int:
        return 0 if self.missing == "api_read" else 1


def _clock() -> Any:
    ticks = iter(range(0, 10_000, 100))
    return lambda: float(next(ticks))


def _run(backend: FakeBackend, inputs: list[dict[str, Any]] | None = None) -> Any:
    return vl.run_hops(
        backend,
        "maps",
        inputs or [{"url": "x"}],
        timeout_seconds=200,
        sleep=lambda _s: None,
        clock=_clock(),
    )


def test_refuses_in_mock_mode(capsys: pytest.CaptureFixture[str]) -> None:
    code = vl.main(["--source", "maps"], environ=_env(INGEST_MODE="mock"), backend=FakeBackend())
    assert code == 2
    assert "mock" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["SOURCE_MAPS_DATASET_ID", "BRIGHT_DATA_API_TOKEN"])
def test_refuses_missing_variable_by_name_only(name: str) -> None:
    values = _env()
    values[name] = ""
    reason = vl.refusal(values, "maps")
    assert reason is not None and name in reason and SENTINEL not in reason


def test_refuses_unknown_source() -> None:
    assert "unknown source" in (vl.refusal(_env(), "rues") or "")


def test_all_hops_pass_and_inputs_are_capped() -> None:
    backend = FakeBackend()
    results, snapshot = _run(backend, [{"i": n} for n in range(9)])
    assert [r.hop for r in results] == list(vl.HOPS)
    assert all(r.status == "pass" for r in results)
    assert snapshot == "snap-1"
    assert len(backend.triggered[0]) == vl.MAX_RECORDS


def test_failure_skips_later_hops_and_prints_next_step() -> None:
    results, _ = _run(FakeBackend(missing="resolve"))
    status = {r.hop: r.status for r in results}
    assert status["normalize"] == "pass"
    assert status["resolve"] == "fail"
    assert status["signal"] == status["score"] == status["api_read"] == "skipped"
    lines = vl.format_report("maps", results)
    assert "Result: FAIL" in lines
    assert any(line.startswith("Next step:") for line in lines)


def test_trigger_failure_skips_everything() -> None:
    results, snapshot = _run(FakeBackend(trigger_error=True))
    assert results[0].status == "fail" and snapshot is None
    assert all(r.status == "skipped" for r in results[1:])


def test_main_pass_captures_mappings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[tuple[str, list[dict[str, Any]]]] = []
    monkeypatch.setattr(
        vl, "capture_mappings", lambda source, records: captured.append((source, records)) or 0
    )
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps([{"url": "x"}]), encoding="utf-8")
    code = vl.main(
        ["--source", "maps", "--inputs", str(inputs), "--timeout", "1"],
        environ=_env(),
        backend=FakeBackend(),
        sleep=lambda _s: None,
    )
    out = capsys.readouterr().out
    assert code == 0 and "Result: PASS" in out and SENTINEL not in out
    assert captured == [("maps", [{"name": "Acme"}])]


def test_main_failure_does_not_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(vl, "capture_mappings", lambda *_a: called.append(True) or 0)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps([{"url": "x"}]), encoding="utf-8")
    monkeypatch.setattr(vl.time, "monotonic", _clock())
    code = vl.main(
        ["--source", "maps", "--inputs", str(inputs), "--timeout", "1"],
        environ=_env(),
        backend=FakeBackend(missing="score"),
        sleep=lambda _s: None,
    )
    assert code == 1 and not called


def test_main_requires_inputs(capsys: pytest.CaptureFixture[str]) -> None:
    assert vl.main(["--source", "maps"], environ=_env(), backend=FakeBackend()) == 2
    assert "--inputs" in capsys.readouterr().out


def test_live_backend_honours_service_url_overrides() -> None:
    backend = vl.LiveBackend(
        _env(
            VERIFY_LIVE_INGEST_URL="http://ingest:8000",
            VERIFY_LIVE_CORE_URL="http://core:8000",
            VERIFY_LIVE_DATABASE_URL="postgresql+asyncpg://u:p@postgres:5432/d",
        )
    )
    assert backend._ingest == "http://ingest:8000"
    assert backend._core == "http://core:8000"
    assert backend._database.endswith("@postgres:5432/d")
