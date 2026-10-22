"""Define environment configuration for every repository process.

Pydantic Settings reads variables from the environment and ``.env``;
``get_settings`` caches them so each process shares one settings instance. It
relates to ``.env.example``, ``docker-compose.yml``, and the ``main.py`` and
worker modules in each service. Its purpose is to keep URLs, local credentials,
logging, and versions outside business code.
"""

from functools import lru_cache
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SOURCE_NAMES = ("maps", "computrabajo", "linkedin_companies", "linkedin_jobs", "instagram", "rues")
BLANK_IS_UNSET = frozenset(
    {
        "database_url",
        "edge_secret",
        "bright_data_webhook_secret",
        "bright_data_api_token",
        "public_webhook_base_url",
        "hot_lead_webhook_url",
    }
)


class SettingsError(RuntimeError):
    """Raise when configuration is unusable; the message names variables, never values."""


class Settings(BaseSettings):
    service_name: str = "invent-market-tracker"
    app_version: str = "dev"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    internal_service_token: str = "local-development-token"
    ingest_url: str = "http://localhost:8001"
    core_url: str = "http://localhost:8002"
    ml_url: str = "http://localhost:8004"
    identity_url: str = "http://localhost:8005"
    pulse_url: str = "http://localhost:8006"
    brief_url: str = "http://localhost:8007"
    streamlit_url: str = "http://localhost:8501"
    edge_secret: str | None = None
    edge_secret_required: bool = False
    csrf_required: bool = False
    identity_validation_enabled: bool = False
    bright_data_webhook_secret: str | None = None
    bright_data_replay_window_seconds: int = Field(default=300, ge=1)
    rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_fail_open: bool = True
    trust_proxy_headers: bool = False
    upstream_timeout_seconds: float = Field(default=10, gt=0)
    readiness_timeout_seconds: float = Field(default=2, gt=0)
    upstream_retry_attempts: int = Field(default=2, ge=1)
    worker_retry_attempts: int = Field(default=3, ge=1)
    database_pool_timeout_seconds: float = Field(default=5, gt=0)
    database_connect_timeout_seconds: float = Field(default=5, gt=0)
    redis_socket_timeout_seconds: float = Field(default=10, gt=0)
    redis_socket_connect_timeout_seconds: float = Field(default=5, gt=0)
    database_pool_size: int = Field(default=4, ge=1)
    database_max_overflow: int = Field(default=0, ge=0)
    database_ssl_mode: str = "prefer"
    ingest_enqueue_mark_ttl_seconds: int = Field(default=86_400, ge=1)
    score_lock_ttl_seconds: int = Field(default=30, ge=1)
    worker_stream_block_ms: int = Field(default=5_000, ge=1)
    worker_failure_sleep_seconds: float = Field(default=1, ge=0)
    outbox_poll_interval_seconds: float = Field(default=1, ge=0)
    event_chain_emit: bool = False
    upstream_retry_base_delay_seconds: float = Field(default=0.1, ge=0)
    upstream_retry_max_delay_seconds: float = Field(default=2, ge=0)
    upstream_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    worker_retry_base_delay_seconds: float = Field(default=1, ge=0)
    worker_retry_max_delay_seconds: float = Field(default=30, ge=0)
    worker_retry_jitter_ratio: float = Field(default=0.25, ge=0, le=1)
    ingest_mode: Literal["mock", "live"] = "mock"
    ingest_poll_enabled: bool = True
    ingest_poll_interval_seconds: int = Field(default=300, ge=1)
    source_maps_enabled: bool = True
    source_maps_dataset_id: str | None = None
    source_computrabajo_enabled: bool = True
    source_computrabajo_dataset_id: str | None = None
    source_linkedin_companies_enabled: bool = False
    source_linkedin_companies_dataset_id: str | None = None
    source_linkedin_jobs_enabled: bool = False
    source_linkedin_jobs_dataset_id: str | None = None
    source_instagram_enabled: bool = False
    source_instagram_dataset_id: str | None = None
    source_rues_enabled: bool = False
    source_rues_dataset_id: str | None = None
    bright_data_api_url: str = "https://api.brightdata.com"
    bright_data_api_token: str | None = None
    public_webhook_base_url: str | None = None
    hot_lead_webhook_url: str | None = None
    socio_data_dir: str = "data/socio"
    app_profile: Literal["local", "production"] = "local"
    frontend_origin: str = "http://localhost:3000"
    rues_delta_url: str = "http://localhost:8000/mocks/rues/delta"
    ops_alert_from: str = "ops@localhost"
    ops_alert_to: str = "ops@localhost"
    dev_user_password_admin: str | None = None
    dev_user_password_strategist: str | None = None
    dev_user_password_intake: str | None = None
    dev_user_password_bd_rep: str | None = None
    dev_user_password_analyst: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def blank_string_means_unset(cls, value: Any, info: ValidationInfo) -> Any:
        """Treat an empty optional variable such as ``TOKEN=`` as unset."""
        name = info.field_name or ""
        optional = (
            name in BLANK_IS_UNSET
            or name.endswith("_dataset_id")
            or name.startswith("dev_user_password_")
        )
        if optional and isinstance(value, str) and value.strip() == "":
            return None
        return value

    def enabled_sources(self) -> list[str]:
        """Return the names of the sources whose ``SOURCE_<NAME>_ENABLED`` flag is on."""
        return [name for name in SOURCE_NAMES if getattr(self, f"source_{name}_enabled")]

    def missing_live_variables(self) -> list[str]:
        """Name every variable that live ingestion needs but lacks; never return a value."""
        if self.ingest_mode != "live":
            return []
        missing = [
            name
            for name in ("BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_WEBHOOK_SECRET")
            if not getattr(self, name.lower())
        ]
        for source in self.enabled_sources():
            if not getattr(self, f"source_{source}_dataset_id"):
                missing.append(f"SOURCE_{source.upper()}_DATASET_ID")
        return missing

    @model_validator(mode="after")
    def validate_runtime_intervals(self) -> Self:
        if self.redis_socket_timeout_seconds * 1000 <= self.worker_stream_block_ms:
            raise ValueError("Redis socket timeout must exceed the stream block interval")
        if self.upstream_retry_max_delay_seconds < self.upstream_retry_base_delay_seconds:
            raise ValueError("upstream retry max delay must cover its base delay")
        if self.worker_retry_max_delay_seconds < self.worker_retry_base_delay_seconds:
            raise ValueError("worker retry max delay must cover its base delay")
        if self.app_profile == "production" and not urlsplit(self.redis_url).username:
            raise ValueError("REDIS_URL must name an ACL user in the production profile")
        if self.edge_secret_required and not self.edge_secret:
            raise ValueError("EDGE_SECRET is required when EDGE_SECRET_REQUIRED is enabled")
        if self.database_ssl_mode not in {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("DATABASE_SSL_MODE must be a valid PostgreSQL sslmode")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    missing = settings.missing_live_variables()
    if missing:
        raise SettingsError("INGEST_MODE=live requires: " + ", ".join(missing))
    return settings
