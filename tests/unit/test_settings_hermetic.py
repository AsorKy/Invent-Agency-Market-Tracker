"""A developer's local ``.env`` must not change unit test results.

It relates to ``tests/conftest.py`` (which switches the dotenv source off) and
``libs/common/settings.py``.
"""

from pathlib import Path

import pytest

from libs.common.settings import Settings, get_settings


def test_a_dotenv_in_the_working_directory_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("OPS_ALERT_TO=from-dotenv@example.test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPS_ALERT_TO", raising=False)
    assert get_settings().ops_alert_to == "ops@localhost"


def test_an_explicit_dotenv_still_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / "custom.env"
    dotenv.write_text("OPS_ALERT_TO=explicit@example.test\n", encoding="utf-8")
    monkeypatch.delenv("OPS_ALERT_TO", raising=False)
    assert Settings(_env_file=dotenv).ops_alert_to == "explicit@example.test"  # type: ignore[call-arg]
