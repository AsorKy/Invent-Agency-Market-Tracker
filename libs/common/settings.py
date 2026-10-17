"""Define environment configuration for every repository process.

Pydantic Settings reads variables from the environment and ``.env``;
``get_settings`` caches them so each process shares one settings instance. It
relates to ``.env.example``, ``docker-compose.yml``, and the ``main.py`` and
worker modules in each service. Its purpose is to keep URLs, local credentials,
logging, and versions outside business code.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "invent-market-tracker"
    app_version: str = "dev"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    internal_service_token: str = "local-development-token"
    gateway_port: int = 8000
    ingest_url: str = "http://localhost:8001"
    core_url: str = "http://localhost:8002"
    analytics_url: str = "http://localhost:8003"
    ml_url: str = "http://localhost:8004"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
