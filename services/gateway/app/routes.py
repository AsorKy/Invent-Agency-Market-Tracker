"""Public route table of the gateway (REQ-API-002).

Maps ``/api/v1/<path>`` to an internal service URL. Only the routes listed here
are reachable: ``/internal/*`` and ``/admin`` of any service never resolve, and
the identity service exposes only login, profile, and logout. Resources that
belong to layers L2 and L3 answer ``not_in_layer`` until their run. There is no
route to the service that ``pulse`` replaced.
Relates to ``services/gateway/app/main.py`` and ``libs.common.settings``.
"""

from dataclasses import dataclass
from typing import Literal

from libs.common.settings import Settings

# First path segments of L2 (ADR-IMT-0002 lens) and L3 (ADR-IMT-0003 prediction) resources.
NOT_IN_LAYER: frozenset[str] = frozenset(
    {
        "lens",
        "funnel",
        "validation",
        "attribution",
        "causal",
        "drift",
        "metric-packs",
        "gates",
        "predictions",
        "forecasts",
    }
)

# Per-route upstream timeouts in seconds, keyed by service or "service:resource".
ROUTE_TIMEOUTS: dict[str, float] = {
    "identity": 5.0,
    "core": 10.0,
    "core:imports": 60.0,
    "pulse": 10.0,
    "brief": 15.0,
    "ingest": 15.0,
    "ml": 30.0,
}

_IDENTITY_ROUTES: dict[str, frozenset[str]] = {
    "auth/session": frozenset({"POST"}),
    "me": frozenset({"GET"}),
    "logout": frozenset({"POST"}),
}
# Identity routes that carry their own cookie check and need no JWT at the gateway.
IDENTITY_PUBLIC_PATHS: frozenset[str] = frozenset(_IDENTITY_ROUTES)

_CORE_RESOURCES = frozenset({"companies", "leads", "pipeline", "projects", "signals"})
_PULSE_RESOURCES = frozenset({"market", "segments"})


@dataclass(frozen=True, slots=True)
class Route:
    """A resolved public route."""

    url: str
    service: str
    timeout_key: str
    public_path: str
    admin_only: bool = False


class NotInLayer:
    """Marker for a resource that is not implemented in the current layer."""


Resolution = Route | NotInLayer | None | Literal["method_not_allowed"]


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def resolve(path: str, method: str, settings: Settings) -> Resolution:
    """Resolve ``path`` (without ``/api/v1/``) and ``method`` to a route."""
    path = path.strip("/")
    first, _, suffix = path.partition("/")
    if first in NOT_IN_LAYER:
        return NotInLayer()
    if path in _IDENTITY_ROUTES:
        if method.upper() not in _IDENTITY_ROUTES[path]:
            return "method_not_allowed"
        return Route(
            _join(settings.identity_url, f"/internal/identity/{path}"), "identity", "identity", path
        )
    if first == "admin":
        if path != "admin/tenant-config":
            return None
        if method.upper() not in {"GET", "PUT"}:
            return "method_not_allowed"
        return Route(
            _join(settings.core_url, "/internal/core/admin/tenant-config"),
            "core",
            "core",
            path,
            admin_only=True,
        )
    if first == "pulse":
        internal = f"/internal/pulse/{suffix}" if suffix else "/internal/pulse"
        return Route(_join(settings.pulse_url, internal), "pulse", "pulse", path)
    if first in _PULSE_RESOURCES or first in {"territory", "ops"}:
        return Route(_join(settings.pulse_url, f"/internal/pulse/{path}"), "pulse", "pulse", path)
    if first == "leads" and suffix.split("/")[-1:] == ["brief"]:
        lead_path = suffix.rsplit("/", 1)[0]
        return Route(
            _join(settings.brief_url, f"/internal/brief/leads/{lead_path}"), "brief", "brief", path
        )
    if first == "brief":
        internal = f"/internal/brief/{suffix}" if suffix else "/internal/brief"
        return Route(_join(settings.brief_url, internal), "brief", "brief", path)
    if first in {"imports", "jobs"}:
        key = "core:imports" if first == "imports" else "core"
        return Route(_join(settings.core_url, f"/internal/core/{path}"), "core", key, path)
    if first in _CORE_RESOURCES:
        return Route(_join(settings.core_url, f"/internal/core/{path}"), "core", "core", path)
    if first == "ingest":
        internal = f"/internal/ingest/{suffix}" if suffix else "/internal/ingest"
        return Route(_join(settings.ingest_url, internal), "ingest", "ingest", path)
    if first == "scores":
        return Route(_join(settings.ml_url, f"/internal/ml/{path}"), "ml", "ml", path)
    return None
