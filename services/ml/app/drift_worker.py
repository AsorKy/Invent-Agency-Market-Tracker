"""Nightly drift worker (Dx3, REQ-LNS-007): one run, no loop.

``run_drift`` loads a reference and a current sample per feature through an
injected loader, computes PSI and CUSUM change points with
``libs/domain/drift.py``, and posts one alert to the n8n webhook when any
feature is ``watch`` or ``retrain``. The URL comes from the optional
``DRIFT_ALERT_WEBHOOK_URL`` variable; when it is unset no alert is sent and
nothing fails. The external scheduler owns the cadence. It relates to
``schedule.py`` and the injected HTTP client protocol of ``publisher.py``.
"""

import math
import os
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from libs.domain import drift

WEBHOOK_ENV = "DRIFT_ALERT_WEBHOOK_URL"
FeatureLoader = Callable[[], Mapping[str, tuple[Sequence[float], Sequence[float]]]]


class AlertClient(Protocol):
    async def post(self, url: str, *, json: Any) -> Any: ...


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
        for name, (ref, cur) in sorted(loader().items())
        if ref and cur
    ]
    flagged = [item for item in results if item.status != "stable"]
    target = url if url is not None else webhook_url()
    if flagged and target:
        body = {"run_id": run_id, "features": [asdict(item) for item in flagged]}
        response = await client.post(target, json=body)
        response.raise_for_status()
    return results
