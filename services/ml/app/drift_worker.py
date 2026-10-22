"""Nightly drift worker (Dx3, REQ-LNS-007): one run, no loop.

``run_drift`` loads a reference and a current sample per feature through an
async loader, computes PSI and CUSUM change points with
``libs/domain/drift.py``, and posts one alert to the n8n webhook when any
feature is ``watch`` or ``retrain``. ``PulseSeriesLoader`` is the real loader:
it reads the weekly unprocessed-signal series from pulse through its API with
a freshly signed service token (``libs/common/internal_auth.py``) and the
``X-Tenant-Id`` header, then splits it into reference and current windows.
The alert URL comes from the optional ``DRIFT_ALERT_WEBHOOK_URL`` variable;
when it is unset no alert is sent and nothing fails. When it is set, the
header name and value of the webhook come from ``DRIFT_ALERT_HEADER_NAME`` and
``DRIFT_ALERT_HEADER_VALUE`` (never a literal); if either is missing the post
is refused. ``schedule.py`` owns the nightly cadence.
"""

import math
import os
import statistics
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from libs.common.internal_auth import service_token_header
from libs.domain import drift

WEBHOOK_ENV = "DRIFT_ALERT_WEBHOOK_URL"
HEADER_NAME_ENV = "DRIFT_ALERT_HEADER_NAME"
HEADER_VALUE_ENV = "DRIFT_ALERT_HEADER_VALUE"
TENANT_ENVS = ("DRIFT_TENANT_ID", "INGEST_TENANT_ID")
ML_SERVICE = "ml"
SIGNALS_PATH = "/internal/pulse/unprocessed-signals"
FeatureSamples = Mapping[str, tuple[Sequence[float], Sequence[float]]]
FeatureLoader = Callable[[], Awaitable[FeatureSamples]]


class AlertClient(Protocol):
    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> Any: ...


class PulseClient(Protocol):
    async def get(self, url: str, *, params: Any, headers: dict[str, str]) -> Any: ...


class DriftConfigError(RuntimeError):
    """Raise when the alert webhook is set but its auth header is not."""


@dataclass(frozen=True, slots=True)
class FeatureDrift:
    feature: str
    psi: float
    status: str
    change_points: list[int] = field(default_factory=list)


def webhook_url() -> str | None:
    """Return the alert webhook URL, or None when unset or blank."""
    value = os.environ.get(WEBHOOK_ENV, "").strip()
    return value or None


def webhook_headers() -> dict[str, str]:
    """Return the webhook auth header from the environment; raise when incomplete."""
    name = os.environ.get(HEADER_NAME_ENV, "").strip()
    value = os.environ.get(HEADER_VALUE_ENV, "")
    if not name or not value:
        raise DriftConfigError(f"{HEADER_NAME_ENV} and {HEADER_VALUE_ENV} must be set")
    return {name: value}


def tenant_id() -> str:
    """Return the tenant the worker reads for, from the environment."""
    for env in TENANT_ENVS:
        value = os.environ.get(env, "").strip()
        if value:
            return value
    raise DriftConfigError(f"{TENANT_ENVS[0]} must be set")


class PulseSeriesLoader:
    """Load drift samples from pulse: older points are the reference, the last ``window`` current.

    ``client`` is an ``httpx.AsyncClient`` with ``base_url`` set to pulse. A fresh
    signed token is minted per request; non-2xx answers raise.
    """

    def __init__(
        self,
        client: PulseClient,
        service_secret: str,
        tenant: str,
        *,
        window: int = 4,
        min_reference: int = 4,
    ) -> None:
        self._client = client
        self._secret = service_secret
        self._tenant = tenant
        self._window = window
        self._min_reference = min_reference

    async def __call__(self) -> FeatureSamples:
        headers = {**service_token_header(ML_SERVICE, self._secret), "X-Tenant-Id": self._tenant}
        response = await self._client.get(
            SIGNALS_PATH, params={"granularity": "week"}, headers=headers
        )
        response.raise_for_status()
        points = response.json().get("points", [])
        series = {
            "unprocessed_total": [float(p["total"]) for p in points],
            "unprocessed_rate": [float(p["rate"]) for p in points if p.get("rate") is not None],
        }
        samples: dict[str, tuple[list[float], list[float]]] = {}
        for name, values in series.items():
            cut = len(values) - self._window
            if cut >= self._min_reference:
                samples[name] = (values[:cut], values[cut:])
        return samples


def analyze_feature(
    name: str, reference: Sequence[float], current: Sequence[float]
) -> FeatureDrift:
    """PSI plus CUSUM (target = reference mean, k = 0.5 sd, h = 5 sd) for one feature."""
    sd = statistics.pstdev(reference) if len(reference) > 1 else 0.0
    sd = sd if sd > 0 and math.isfinite(sd) else 1.0
    value = drift.psi(reference, current)
    points = drift.cusum(current, target=statistics.fmean(reference), k=0.5 * sd, h=5 * sd)
    return FeatureDrift(name, value, drift.classify_psi(value), points)


async def run_drift(
    loader: FeatureLoader, client: AlertClient, *, run_id: str, url: str | None = None
) -> list[FeatureDrift]:
    """Compute drift for every feature and alert on watch or retrain."""
    results = [
        analyze_feature(name, ref, cur)
        for name, (ref, cur) in sorted((await loader()).items())
        if ref and cur
    ]
    flagged = [item for item in results if item.status != "stable"]
    target = url if url is not None else webhook_url()
    if flagged and target:
        body = {"run_id": run_id, "features": [asdict(item) for item in flagged]}
        response = await client.post(target, json=body, headers=webhook_headers())
        response.raise_for_status()
    return results
