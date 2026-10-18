"""Test the run 1 environment surface declared in ``libs/common/settings.py``.

It covers the ingest mode switch, per-source flags, blank optional variables, and
the fail-fast rule for live mode: errors name variables and never show values.
It relates to ``.env.example`` and ``docker-compose.yml`` through the configuration guard.
"""

import pytest

pytest.importorskip("pydantic_settings")

from libs.common.settings import SOURCE_NAMES, Settings, SettingsError, get_settings  # noqa: E402

SOURCE_VARIABLES = [f"SOURCE_{name.upper()}_DATASET_ID" for name in SOURCE_NAMES]
SECRET_VALUE = "super-secret-value-123"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "INGEST_MODE",
        "BRIGHT_DATA_API_TOKEN",
        "BRIGHT_DATA_WEBHOOK_SECRET",
        "APP_PROFILE",
        *SOURCE_VARIABLES,
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir("/")  # no .env file is picked up


def test_defaults_are_local_and_mock() -> None:
    settings = Settings()
    assert settings.ingest_mode == "mock"
    assert settings.app_profile == "local"
    assert settings.enabled_sources() == ["maps", "computrabajo"]
    assert settings.missing_live_variables() == []


def test_blank_optional_variables_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIGHT_DATA_API_TOKEN", "")
    monkeypatch.setenv("SOURCE_MAPS_DATASET_ID", "")
    settings = Settings()
    assert settings.bright_data_api_token is None
    assert settings.source_maps_dataset_id is None


def test_live_mode_names_every_missing_variable_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_MODE", "live")
    monkeypatch.setenv("SOURCE_MAPS_DATASET_ID", "gd_maps")
    monkeypatch.setenv("BRIGHT_DATA_WEBHOOK_SECRET", SECRET_VALUE)
    get_settings.cache_clear()
    with pytest.raises(SettingsError) as error:
        get_settings()
    message = str(error.value)
    assert "BRIGHT_DATA_API_TOKEN" in message
    assert "SOURCE_COMPUTRABAJO_DATASET_ID" in message
    assert "SOURCE_MAPS_DATASET_ID" not in message
    assert SECRET_VALUE not in message and "gd_maps" not in message


def test_live_mode_passes_when_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_MODE", "live")
    monkeypatch.setenv("BRIGHT_DATA_API_TOKEN", "t")
    monkeypatch.setenv("BRIGHT_DATA_WEBHOOK_SECRET", "w")
    monkeypatch.setenv("SOURCE_MAPS_DATASET_ID", "a")
    monkeypatch.setenv("SOURCE_COMPUTRABAJO_DATASET_ID", "b")
    get_settings.cache_clear()
    assert get_settings().ingest_mode == "live"


def test_disabled_source_needs_no_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_MODE", "live")
    monkeypatch.setenv("BRIGHT_DATA_API_TOKEN", "t")
    monkeypatch.setenv("BRIGHT_DATA_WEBHOOK_SECRET", "w")
    monkeypatch.setenv("SOURCE_MAPS_ENABLED", "false")
    monkeypatch.setenv("SOURCE_COMPUTRABAJO_DATASET_ID", "b")
    assert Settings().missing_live_variables() == []


def test_invalid_mode_error_hides_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_MODE", SECRET_VALUE)
    with pytest.raises(ValueError) as error:
        Settings()
    assert SECRET_VALUE not in str(error.value)


def test_profile_is_restricted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "staging")
    with pytest.raises(ValueError):
        Settings()
