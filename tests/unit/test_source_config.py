"""Unit tests for per-source connector selection and live credential checks.

Covers REQ-RUN-004 (``INGEST_MODE`` selects mock or live, a live source without
credentials fails at startup naming the variable and never a value) and the
notify URL built from ``PUBLIC_WEBHOOK_BASE_URL`` (REQ-ING-004). No network.
"""

import httpx
import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("yaml")

from libs.common.settings import Settings, SettingsError  # noqa: E402
from services.ingest.app.connectors import BrightDataConnector, MockConnector  # noqa: E402
from services.ingest.app.source_config import (  # noqa: E402
    build_connector,
    build_connectors,
    notify_url,
    validate_live_credentials,
)

SECRET_VALUE = "sentinel-secret-value"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def live(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "ingest_mode": "live",
        "bright_data_api_token": SECRET_VALUE,
        "bright_data_webhook_secret": SECRET_VALUE,
        "source_maps_dataset_id": "gd_maps",
        "source_computrabajo_dataset_id": "gd_jobs",
        "public_webhook_base_url": "https://pub.test/",
    }
    base.update(overrides)
    return make_settings(**base)


def test_mock_mode_builds_mock_connectors_without_credentials() -> None:
    connectors = build_connectors(make_settings())
    assert set(connectors) == {"maps", "computrabajo"}
    assert all(isinstance(c, MockConnector) for c in connectors.values())


def test_live_mode_builds_bright_data_connectors() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    connectors = build_connectors(live(), client=client)
    assert all(isinstance(c, BrightDataConnector) for c in connectors.values())
    assert connectors["maps"].dataset_id == "gd_maps"


def test_live_mode_skips_sources_without_mapping_and_rues() -> None:
    settings = live(
        source_instagram_enabled=True,
        source_instagram_dataset_id="gd_ig",
        source_rues_enabled=True,
        source_rues_dataset_id="gd_rues",
    )
    assert set(build_connectors(settings)) == {"maps", "computrabajo"}
    with pytest.raises(ValueError):
        build_connector(settings, "rues")


@pytest.mark.parametrize(
    ("override", "variable"),
    [
        ({"bright_data_api_token": None}, "BRIGHT_DATA_API_TOKEN"),
        ({"bright_data_webhook_secret": None}, "BRIGHT_DATA_WEBHOOK_SECRET"),
        ({"source_maps_dataset_id": None}, "SOURCE_MAPS_DATASET_ID"),
    ],
)
def test_live_without_credentials_fails_naming_variable(
    override: dict[str, object], variable: str
) -> None:
    settings = live(**override)
    with pytest.raises(SettingsError) as info:
        build_connectors(settings)
    assert variable in str(info.value)
    assert SECRET_VALUE not in str(info.value)


def test_live_needs_webhook_url_or_polling() -> None:
    settings = live(public_webhook_base_url=None, ingest_poll_enabled=False)
    with pytest.raises(SettingsError, match="PUBLIC_WEBHOOK_BASE_URL"):
        validate_live_credentials(settings)
    validate_live_credentials(live(public_webhook_base_url=None, ingest_poll_enabled=True))


def test_mock_mode_never_validates_live_variables() -> None:
    validate_live_credentials(make_settings(bright_data_api_token=None))


def test_notify_url_is_built_from_public_base() -> None:
    assert notify_url(live()) == "https://pub.test/internal/ingest/webhook/bd"
    assert notify_url(make_settings()) is None
