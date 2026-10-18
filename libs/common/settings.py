"""Define environment configuration for every repository process.

Pydantic Settings reads variables from the environment and ``.env``;
``get_settings`` caches them so each process shares one settings instance. It
relates to ``.env.example``, ``docker-compose.yml``, and the ``main.py`` and
worker modules in each service. Its purpose is to keep URLs, local credentials,
logging, and versions outside business code.
"""

from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    analytics_url: str = "http://localhost:8003"
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
    upstream_retry_base_delay_seconds: float = Field(default=0.1, ge=0)
    upstream_retry_max_delay_seconds: float = Field(default=2, ge=0)
    upstream_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    worker_retry_base_delay_seconds: float = Field(default=1, ge=0)
    worker_retry_max_delay_seconds: float = Field(default=30, ge=0)
    worker_retry_jitter_ratio: float = Field(default=0.25, ge=0, le=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def validate_runtime_intervals(self) -> Self:
        if self.redis_socket_timeout_seconds * 1000 <= self.worker_stream_block_ms:
            raise ValueError("Redis socket timeout must exceed the stream block interval")
        if self.upstream_retry_max_delay_seconds < self.upstream_retry_base_delay_seconds:
            raise ValueError("upstream retry max delay must cover its base delay")
        if self.worker_retry_max_delay_seconds < self.worker_retry_base_delay_seconds:
            raise ValueError("worker retry max delay must cover its base delay")
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
    return Settings()
