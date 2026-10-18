"""Select the mock or live connector per source and check live credentials.

``INGEST_MODE`` (REQ-RUN-004) chooses ``MockConnector`` or ``BrightDataConnector``
for every enabled source. ``validate_live_credentials`` fails fast at startup
with a ``SettingsError`` that names the missing variables and never shows a
value. ``notify_url`` builds the webhook URL from ``PUBLIC_WEBHOOK_BASE_URL``.
Sources without a mapping file yet (added by later tasks) and ``rues`` (lookup
interface only) get no connector. It relates to ``connectors.py``, ``poll.py``,
and ``libs/common/settings.py``.
"""

import httpx

from libs.common.settings import Settings, SettingsError
from services.ingest.app.connectors import (
    BrightDataConnector,
    MockConnector,
    SourceConnector,
)
from services.ingest.app.mapping import MAPPINGS_DIR, load_mapping

WEBHOOK_PATH = "/internal/ingest/webhook/bd"
NO_CONNECTOR_SOURCES = frozenset({"rues"})
MOCK_BASE_URL = "http://mocks:8000"


def notify_url(settings: Settings) -> str | None:
    """Return the public webhook URL for Bright Data, or None when not configured."""
    base = settings.public_webhook_base_url
    if not base:
        return None
    return base.rstrip("/") + WEBHOOK_PATH


def validate_live_credentials(settings: Settings) -> None:
    """Raise ``SettingsError`` naming every missing live variable (never values).

    Live mode needs the API token, the webhook secret, a dataset ID per enabled
    source, and a way to learn about ready snapshots: a public webhook base URL
    or polling.
    """
    if settings.ingest_mode != "live":
        return
    missing = settings.missing_live_variables()
    if not settings.public_webhook_base_url and not settings.ingest_poll_enabled:
        missing.append("PUBLIC_WEBHOOK_BASE_URL")
    if missing:
        raise SettingsError("INGEST_MODE=live requires: " + ", ".join(missing))


def build_connector(
    settings: Settings, source: str, *, client: httpx.Client | None = None
) -> SourceConnector:
    """Build the connector of one source for the configured mode.

    ``client`` is injectable for tests; by default a client with the configured
    timeout is created against the mock service or the Bright Data API.
    """
    if source in NO_CONNECTOR_SOURCES:
        raise ValueError(f"source {source} has no connector")
    mapping = load_mapping(source)
    dataset_id = getattr(settings, f"source_{source}_dataset_id", None)
    if settings.ingest_mode == "live":
        validate_live_credentials(settings)
        http = client or httpx.Client(
            base_url=settings.bright_data_api_url, timeout=settings.upstream_timeout_seconds
        )
        return BrightDataConnector(
            source,
            http,
            dataset_id=str(dataset_id),
            api_token=str(settings.bright_data_api_token),
            mapping=mapping,
            retry_attempts=settings.upstream_retry_attempts,
            retry_base_delay_seconds=settings.upstream_retry_base_delay_seconds,
            retry_max_delay_seconds=settings.upstream_retry_max_delay_seconds,
        )
    http = client or httpx.Client(base_url=MOCK_BASE_URL, timeout=settings.upstream_timeout_seconds)
    return MockConnector(source, http, dataset_id=dataset_id, mapping=mapping)


def build_connectors(
    settings: Settings, *, client: httpx.Client | None = None
) -> dict[str, SourceConnector]:
    """Build connectors for every enabled source that has a mapping file."""
    validate_live_credentials(settings)
    connectors: dict[str, SourceConnector] = {}
    for source in settings.enabled_sources():
        if source in NO_CONNECTOR_SOURCES:
            continue
        if not (MAPPINGS_DIR / f"{source}.yaml").exists():
            continue
        connectors[source] = build_connector(settings, source, client=client)
    return connectors
