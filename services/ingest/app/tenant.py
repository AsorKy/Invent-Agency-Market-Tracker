"""Ingest tenant configuration (REQ-ING-009, REQ-EVT-005).

Ingest writes on behalf of one tenant. The tenant comes from the
``INGEST_TENANT_ID`` variable and never from a Bright Data payload. A missing
or malformed value fails fast at startup. It relates to ``to_core.py`` and
``processor.py``.
"""

from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestTenantSettings(BaseSettings):
    """Reads ``INGEST_TENANT_ID``; required, so ingest cannot start without it."""

    ingest_tenant_id: UUID

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )


def load_ingest_tenant_id() -> UUID:
    """Return the configured ingest tenant id."""
    return IngestTenantSettings().ingest_tenant_id  # type: ignore[call-arg]
