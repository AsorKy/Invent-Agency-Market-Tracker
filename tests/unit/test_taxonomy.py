"""Test the data taxonomy helpers and the migration column-type rule (REQ-DAT-003)."""

import re
import subprocess
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from libs.common.money import money_to_str, parse_money
from libs.common.time import from_epoch_ms, local_day, utc_epoch_ms, utc_now_ms

ROOT = Path(__file__).resolve().parents[2]


def test_local_day_uses_bogota_zone() -> None:
    ts = datetime(2026, 10, 18, 3, 30, tzinfo=UTC)  # 22:30 previous day in Bogota
    assert local_day(ts, "medellin") == date(2026, 10, 17)
    assert local_day(ts) == date(2026, 10, 17)
    assert local_day(ts.replace(hour=5, minute=0), "Bogota") == date(2026, 10, 18)


def test_local_day_accepts_any_offset_and_rejects_naive() -> None:
    plus = timezone(timedelta(hours=9))
    assert local_day(datetime(2026, 10, 18, 9, 0, tzinfo=plus), "cali") == date(2026, 10, 17)
    with pytest.raises(ValueError):
        local_day(datetime(2026, 10, 18, 9, 0), "cali")


def test_epoch_ms_roundtrip() -> None:
    ts = datetime(2026, 10, 17, 15, 30, 0, 123000, tzinfo=UTC)
    ms = utc_epoch_ms(ts)
    assert ms == 1792251000123
    assert from_epoch_ms(ms) == ts
    with pytest.raises(ValueError):
        utc_epoch_ms(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        from_epoch_ms(1.5)  # type: ignore[arg-type]
    assert utc_now_ms() > ms - 10**12


def test_money_is_decimal_string() -> None:
    assert money_to_str("12.5") == "12.50"
    assert money_to_str(3) == "3.00"
    assert money_to_str(Decimal("0.005")) == "0.00"
    assert money_to_str(Decimal("0.015")) == "0.02"
    assert parse_money("-1.239") == Decimal("-1.24")


def test_money_rejects_float_nan_and_overflow() -> None:
    with pytest.raises(TypeError):
        parse_money(1.1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        parse_money("abc")
    with pytest.raises(ValueError):
        parse_money("NaN")
    with pytest.raises(ValueError):
        parse_money("1000000000000.00")


def _migration_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [ROOT / n for n in out if n.startswith("migrations/") and n.endswith(".sql")]


def test_no_json_float_or_real_columns_in_migrations() -> None:
    files = _migration_files()
    assert files, "expected at least one migration"
    banned = re.compile(r"\b(json|float\d*|real|double\s+precision)\b", re.IGNORECASE)
    offenders: list[str] = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("--", 1)[0]
            if banned.search(code):
                offenders.append(f"{path.name}:{number}: {code.strip()}")
    assert not offenders, offenders
