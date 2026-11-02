"""Public route table of the gateway (REQ-API-002).

Maps ``/api/v1/<path>`` to an internal service URL. Only the routes listed here
are reachable: ``/internal/*`` and ``/admin`` of any service never resolve, and
the identity service exposes only login, profile, and logout. Resources that
belong to layers L2 and L3 answer ``not_in_layer`` until their run. There is no
route to the service that ``pulse`` replaced. The read-only data explorer
(``data/catalog`` and ``data/<dataset>``) resolves only through ``data/``: the
catalog fans out to every owning service and a dataset goes to its owner in
``libs.common.data_explorer.CATALOG``; the ``pulse/``, ``brief/`` and
``ingest/`` passthroughs never reach it, and a path with a dot segment never
resolves. Relates to ``services/gateway/app/main.py`` and ``libs.common.settings``.
"""

from dataclasses import dataclass
from typing import Literal

from libs.common.data_explorer import CATALOG, DATA_SERVICES
from libs.common.settings import Settings

# First path segments of L2 (ADR-IMT-0002 lens) and L3 (ADR-IMT-0003 prediction) resources.
NOT_IN_LAYER: frozenset[str] = frozenset(
    {
        "lens",
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
_READ_ROLES = frozenset({"admin", "strategist", "analyst"})
_DECIDE_ROLES = frozenset({"admin", "strategist"})
_PULSE_RESOURCES = frozenset({"market", "segments"})


@dataclass(frozen=True, slots=True)
class Route:
    """A resolved public route."""

    url: str
    service: str
    timeout_key: str
    public_path: str
    admin_only: bool = False
    allowed_roles: frozenset[str] | None = None
    # (service, url) pairs the gateway calls and merges instead of proxying ``url``.
    fanout: tuple[tuple[str, str], ...] = ()


class NotInLayer:
    """Marker for a resource that is not implemented in the current layer."""


Resolution = Route | NotInLayer | None | Literal["method_not_allowed"]


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _lens_route(path: str, method: str, settings: Settings) -> Resolution:
    """Resolve the funnel and validation routes (T-146, T-147); other paths do not resolve."""
    parts = path.split("/")
    internal = f"/internal/core/{path}"
    if parts[0] == "validation" and method.upper() == "GET":
        known = (len(parts) == 2 and parts[1] == "meta-ml") or (
            len(parts) == 4 and parts[1] == "projects"
        )
        roles = _READ_ROLES
    elif parts[0] == "funnel" and len(parts) == 3 and parts[1] == "projects":
        known = method.upper() == "GET"
        roles = _READ_ROLES
    elif (
        parts[0] == "funnel" and len(parts) == 6 and parts[1] == "projects" and parts[3] == "gates"
    ):
        known = parts[5] == "decision" and method.upper() == "POST"
        roles = _DECIDE_ROLES
    else:
        return None
    if not known:
        return None
    return Route(_join(settings.core_url, internal), "core", "core", path, allowed_roles=roles)


def _service_url(settings: Settings, service: str) -> str:
    urls = {
        "ingest": settings.ingest_url,
        "core": settings.core_url,
        "ml": settings.ml_url,
        "brief": settings.brief_url,
        "pulse": settings.pulse_url,
    }
    return urls[service]


def _data_route(path: str, name: str, method: str, settings: Settings) -> Resolution:
    """Resolve the read-only data explorer: the catalog fan-out or one dataset at its owner."""
    if name != "catalog" and name not in CATALOG:
        return None
    if method.upper() != "GET":
        return "method_not_allowed"
    if name == "catalog":
        fanout = tuple(
            (service, _join(_service_url(settings, service), f"/internal/{service}/data/catalog"))
            for service in DATA_SERVICES
        )
        return Route("", "gateway", "gateway", path, allowed_roles=_READ_ROLES, fanout=fanout)
    service = CATALOG[name].service
    internal = f"/internal/{service}/data/{name}"
    return Route(
        _join(_service_url(settings, service), internal),
        service,
        service,
        path,
        allowed_roles=_READ_ROLES,
    )


def resolve(path: str, method: str, settings: Settings) -> Resolution:
    """Resolve ``path`` (without ``/api/v1/``) and ``method`` to a route."""
    path = path.strip("/")
    first, _, suffix = path.partition("/")
    if {".", ".."} & set(path.split("/")):
        # httpx would normalize the segment and could reach another internal path.
        return None
    if first in NOT_IN_LAYER:
        return NotInLayer()
    if first == "data":
        return _data_route(path, suffix, method, settings) if "/" not in suffix else None
    if first in {"pulse", "brief", "ingest"} and suffix.partition("/")[0] == "data":
        # The data explorer keeps its read roles: only ``data/...`` reaches it.
        return None
    if first in {"funnel", "validation"}:
        return _lens_route(path, method, settings)
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
    if first == "compliance":
        if path != "compliance/opt-out":
            return None
        if method.upper() != "POST":
            return "method_not_allowed"
        return Route(
            _join(settings.core_url, "/internal/core/compliance/opt-out"),
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
    if first == "projects" and path.endswith("/validation-inputs") and path.count("/") == 2:
        if method.upper() != "PUT":
            return "method_not_allowed"
        return Route(
            _join(settings.core_url, f"/internal/core/{path}"),
            "core",
            "core",
            path,
            allowed_roles=_DECIDE_ROLES,
        )
    if first in _CORE_RESOURCES:
        return Route(_join(settings.core_url, f"/internal/core/{path}"), "core", "core", path)
    if first == "ingest":
        internal = f"/internal/ingest/{suffix}" if suffix else "/internal/ingest"
        return Route(_join(settings.ingest_url, internal), "ingest", "ingest", path)
    if first == "scores":
        return Route(_join(settings.ml_url, f"/internal/ml/{path}"), "ml", "ml", path)
    return None
