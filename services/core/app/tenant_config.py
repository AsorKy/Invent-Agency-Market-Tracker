"""Validate and version tenant configuration with ETag concurrency.

``TenantConfig`` is the Pydantic model for ``core.tenant_config``: ``scoring_rules``,
``brief_rules``, ``bright_data_budget_usd`` and the reserved ``funnel_rules`` (with
``geo_overrides``) which is stored inside the ``metadata`` JSONB column, so L2 needs no migration
(REQ-EXT-002). Unknown top-level keys are rejected. ``update_config`` compares the ``If-Match``
version, copies the previous version to ``core.tenant_config_history`` and emits
``tenant.config_updated`` in one transaction (REQ-COR-004). It relates to ``main.py`` and
``repository.py``. Each call is O(1) in queries.
"""

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.events import EventType


class AlertRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no_contact_days_warning: int = Field(default=5, ge=0)
    no_contact_days_critical: int = Field(default=15, ge=0)
    require_rues_match: bool = True
    min_score_for_qualified: int = Field(default=50, ge=0, le=100)
    min_score_for_hot: int = Field(default=70, ge=0, le=100)
    max_quarantine_days: int = Field(default=7, ge=0)


class BriefRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_rules: AlertRules = Field(default_factory=AlertRules)
    weights: dict[str, float] = Field(default_factory=dict)
    disabled_alert_codes: list[str] = Field(default_factory=list)


class FunnelRules(BaseModel):
    """Reserved for L2/L3: accepted and stored verbatim, interpreted by later layers."""

    model_config = ConfigDict(extra="allow")

    geo_overrides: dict[str, Any] = Field(default_factory=dict)


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scoring_rules: dict[str, Any] = Field(default_factory=dict)
    brief_rules: BriefRules = Field(default_factory=BriefRules)
    bright_data_budget_usd: float | None = Field(default=None, ge=0)
    funnel_rules: FunnelRules | None = None


class StaleConfigError(Exception):
    """``If-Match`` does not match the stored version (HTTP 412)."""

    def __init__(self, current_version: int) -> None:
        super().__init__("stale_etag")
        self.current_version = current_version


def etag(version: int) -> str:
    return f'"{version}"'


def parse_etag(value: str) -> int:
    """Return the version in an ``If-Match`` value or raise ``ValueError``."""
    cleaned = value.strip()
    if cleaned.startswith("W/"):
        cleaned = cleaned[2:]
    return int(cleaned.strip('"'))


def config_to_row(config: TenantConfig) -> dict[str, Any]:
    """Map the model onto the table columns; ``funnel_rules`` rides in ``metadata``."""
    metadata: dict[str, Any] = {}
    if config.funnel_rules is not None:
        metadata["funnel_rules"] = config.funnel_rules.model_dump(mode="json")
    return {
        "scoring_rules": config.scoring_rules,
        "brief_rules": config.brief_rules.model_dump(mode="json"),
        "bright_data_budget_usd": config.bright_data_budget_usd,
        "metadata": metadata,
    }


def row_to_config(row: dict[str, Any]) -> TenantConfig:
    metadata = row.get("metadata") or {}
    budget = row.get("bright_data_budget_usd")
    return TenantConfig(
        scoring_rules=row.get("scoring_rules") or {},
        brief_rules=BriefRules(**(row.get("brief_rules") or {})),
        bright_data_budget_usd=float(budget) if budget is not None else None,
        funnel_rules=metadata.get("funnel_rules"),
    )


def config_view(version: int, config: TenantConfig) -> dict[str, Any]:
    return {"version": version, **config.model_dump(mode="json")}


class ConfigTransaction(Protocol):
    async def get_config(self, tenant_id: UUID) -> dict[str, Any] | None: ...
    async def insert_config(self, row: dict[str, Any]) -> None: ...
    async def close_version(self, old: dict[str, Any], closed_at: datetime) -> None: ...
    async def update_config(self, row: dict[str, Any]) -> None: ...
    async def add_outbox_event(
        self, *, event_id: UUID, aggregate_id: UUID, version: int, payload: dict[str, Any]
    ) -> None: ...


class ConfigUnitOfWork(Protocol):
    async def __aenter__(self) -> ConfigTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


async def read_config(unit_of_work: ConfigUnitOfWork, tenant_id: UUID) -> dict[str, Any]:
    """Return the stored config, or the defaults at version 0 when none exists yet."""
    async with unit_of_work as tx:
        row = await tx.get_config(tenant_id)
    if row is None:
        return config_view(0, TenantConfig())
    return config_view(int(row["version"]), row_to_config(row))


async def update_config(
    unit_of_work: ConfigUnitOfWork,
    tenant_id: UUID,
    config: TenantConfig,
    *,
    if_match: int,
    updated_by: UUID | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replace the config when ``if_match`` equals the stored version (0 when absent)."""
    moment = now or datetime.now(UTC)
    new_row = config_to_row(config)
    async with unit_of_work as tx:
        old = await tx.get_config(tenant_id)
        current = int(old["version"]) if old else 0
        if if_match != current:
            raise StaleConfigError(current)
        if old is not None and row_to_config(old) == config:
            return config_view(current, config)
        version = current + 1
        row = {
            **new_row,
            "tenant_id": tenant_id,
            "version": version,
            "updated_by": updated_by,
            "updated_at": moment,
        }
        if old is None:
            await tx.insert_config(row)
        else:
            await tx.close_version(old, moment)
            await tx.update_config(row)
        await tx.add_outbox_event(
            event_id=uuid4(),
            aggregate_id=tenant_id,
            version=version,
            payload={
                "tenant_id": str(tenant_id),
                "version": version,
                "event_type": EventType.TENANT_CONFIG_UPDATED.value,
                "request_id": request_id,
            },
        )
        return config_view(version, config)
