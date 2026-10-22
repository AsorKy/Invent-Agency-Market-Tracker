"""Service-layer attribution runner (REQ-LNS-005).

Takes journeys, runs one model from ``libs.domain.attribution``, and returns
rows shaped for ``pulse.attribution``. ``publish_attribution`` posts them to
``POST /internal/pulse/ingest/attribution`` with a signed ml service token and
the tenant in the body and in ``X-Tenant-Id``, through an injectable client with
``post(url, *, json, headers)``; non-2xx answers raise. It relates to
``libs/domain/attribution.py``.
"""

from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from libs.common.internal_auth import service_token_header
from libs.domain import attribution as attr

ATTRIBUTION_PATH = "/internal/pulse/ingest/attribution"
ML_SERVICE = "ml"
MODELS = ("last_touch", "first_touch", "time_decay", "markov", "shapley")


def attribute(
    journeys: Sequence[attr.Journey],
    model: str,
    *,
    half_life_days: float = 7.0,
    shapley_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Return one row per channel: model, channel, credit, and total closes."""
    if model == "last_touch":
        credit = attr.last_touch(journeys)
    elif model == "first_touch":
        credit = attr.first_touch(journeys)
    elif model == "time_decay":
        credit = attr.time_decay(journeys, half_life_days)
    elif model == "markov":
        credit = attr.markov(journeys)
    elif model == "shapley":
        credit = attr.shapley(journeys, enabled=shapley_enabled)
    else:
        raise attr.AttributionError(f"unknown attribution model: {model}")
    closes = attr.count_closes(journeys)
    return [
        {"model": model, "channel": c, "credit": round(v, 9), "conversions": closes}
        for c, v in sorted(credit.items())
    ]


class HttpClient(Protocol):
    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> Any: ...


async def publish_attribution(
    client: HttpClient,
    service_secret: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    source_version: str,
    rows: Sequence[dict[str, Any]],
) -> None:
    """Post the rows of ``attribute`` to pulse; the body carries ``tenant_id``."""
    body = {
        "tenant_id": str(tenant_id),
        "project_id": str(project_id),
        "source_version": source_version,
        "rows": [
            {"model": r["model"], "channel": r["channel"], "credit": r["credit"]} for r in rows
        ],
    }
    headers = service_token_header(ML_SERVICE, service_secret)
    headers["X-Tenant-Id"] = str(tenant_id)
    response = await client.post(ATTRIBUTION_PATH, json=body, headers=headers)
    response.raise_for_status()
