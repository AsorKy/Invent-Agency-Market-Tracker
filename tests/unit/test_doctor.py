"""Unit tests for ops/doctor.py: names only, never a value (REQ-RUN-005)."""

import json
from pathlib import Path

import pytest

from ops import doctor

SENTINEL = "SENTINEL-s3cr3t-9f8e7d"
HOOK = f"https://hooks.example.test/{SENTINEL}"


def _values(**extra: str) -> dict[str, str]:
    values = {name: SENTINEL for name in doctor.REQUIRED}
    values.update(extra)
    return values


def _ready(status: int = 200) -> doctor.Fetch:
    body = json.dumps({"dependencies": {"redis": True, "core": status == 200}})
    return lambda _url: (status, body)


def test_sentinel_never_printed() -> None:
    values = _values(
        EDGE_SECRET=SENTINEL,
        HOT_LEAD_WEBHOOK_URL=HOOK,
        BRIGHT_DATA_API_TOKEN=SENTINEL,
        INGEST_MODE="live",
    )
    lines, _ = doctor.build_report(values, _ready(), lambda _u: True)
    assert SENTINEL not in "\n".join(lines)


def test_main_output_hides_sentinel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"EDGE_SECRET={SENTINEL}\nPOSTGRES_PASSWORD={SENTINEL}\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    monkeypatch.setattr(doctor, "fetch", lambda _u, timeout=3.0: (0, ""))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SENTINEL)
    assert doctor.main() == 1
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert "EDGE_SECRET: set" in captured.out


def test_missing_required_reported_by_name() -> None:
    lines, ok = doctor.build_report({}, _ready(), lambda _u: True)
    assert not ok
    assert "  POSTGRES_PASSWORD: MISSING" in lines
    assert any(line.startswith("Missing: ") for line in lines)


def test_all_set_is_ok_and_sources_listed() -> None:
    values = _values(SOURCE_MAPS_ENABLED="true", SOURCE_RUES_ENABLED="false")
    lines, ok = doctor.build_report(values, _ready(), lambda _u: True)
    assert ok
    assert "  maps: active" in lines
    assert "  rues: inactive" in lines
    assert "Ingest mode: mock" in lines
    assert "  redis: ok" in lines


def test_live_mode_requires_credentials_and_dataset_ids() -> None:
    values = _values(INGEST_MODE="live", SOURCE_MAPS_ENABLED="true")
    lines, ok = doctor.build_report(values, _ready(), lambda _u: True)
    assert not ok
    assert "  BRIGHT_DATA_API_TOKEN: MISSING" in lines
    assert "  maps: active (SOURCE_MAPS_DATASET_ID MISSING)" in lines


def test_gateway_states() -> None:
    down, _ = doctor.build_report(_values(), lambda _u: (0, ""), lambda _u: True)
    assert "  gateway: unreachable (is the stack running?)" in down
    degraded, _ = doctor.build_report(_values(), _ready(503), lambda _u: True)
    assert "  gateway: degraded" in degraded
    assert "  core: DOWN" in degraded


def test_webhook_reachability() -> None:
    values = _values(HOT_LEAD_WEBHOOK_URL=HOOK)
    up, _ = doctor.build_report(values, _ready(), lambda _u: True)
    down, _ = doctor.build_report(values, _ready(), lambda _u: False)
    assert "  HOT_LEAD_WEBHOOK_URL: reachable" in up
    assert "  HOT_LEAD_WEBHOOK_URL: UNREACHABLE" in down
    assert "  PUBLIC_WEBHOOK_BASE_URL: not configured" in up
