"""Explicit registry of the public ``/api/v1`` routes (REQ-API-001, REQ-API-002).

Each ``PublicRoute`` names a method, a path relative to ``/api/v1``, the request
and response models, the error statuses, and the HTTP headers that matter to the
frontend. ``services/gateway/app/openapi_doc.py`` renders the OpenAPI document
from this list and ``ops/export_openapi.py`` writes it to ``docs/api``. The
registry is checked against ``routes.resolve`` so a path the gateway table does
not serve cannot be documented, and the tests fail for a route missing from the
document. Response schemas come from the frozen ``libs/contracts/api.py``; where
the contract has no model the response is a generic JSON object.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.api import (
    BriefResponse,
    ImportJobAccepted,
    ImportJobResponse,
    PipelineBacklogResponse,
    ScoreFeatures,
    ScoreResponse,
    SignalCreate,
    SourceEfficiencyResponse,
    TimeInPipelineResponse,
    TopSourcesResponse,
    UnprocessedSignalsResponse,
)
from services.core.app.actions import ActivityCreate, OutcomeCreate, ProjectCreate
from services.core.app.companies import CompanyUpsert
from services.core.app.leads import LeadCreate, LeadStatusPatch
from services.core.app.tenant_config import TenantConfig
from services.core.app.validation_inputs import ValidationInputs


class ErrorBody(BaseModel):
    """Error body returned by the gateway and by every service."""

    detail: str | dict[str, Any]


class JsonObject(BaseModel):
    """Free-form JSON object for responses the frozen contract does not model."""

    model_config = ConfigDict(extra="allow")


class FunnelDecisionRequest(BaseModel):
    """Body of a funnel gate decision (checklist inputs and optimistic version)."""

    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, Any]
    expected_version: int = Field(ge=1)


class Page(BaseModel):
    """Cursor-paginated list returned by core list endpoints."""

    data: list[dict[str, Any]]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class Param:
    """A query parameter."""

    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    minimum: int | None = None
    maximum: int | None = None
    default: Any = None
    enum: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublicRoute:
    """One public method and path with its contract."""

    method: str
    path: str
    summary: str
    tag: str
    response: type[BaseModel] | None = None
    request: type[BaseModel] | None = None
    request_content: str = "application/json"
    status: int = 200
    query: tuple[Param, ...] = ()
    errors: tuple[int, ...] = ()
    request_headers: tuple[str, ...] = ()
    response_headers: tuple[str, ...] = ()
    public: bool = False
    extra_responses: dict[int, str] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def operation_id(self) -> str:
        parts = [p.strip("{}") + ("_by" if p.startswith("{") else "") for p in self.path.split("/")]
        return self.method.lower() + "_" + "_".join(p.replace("-", "_") for p in parts if p)


PAGING = (
    Param("limit", "integer", minimum=1, maximum=200, default=50),
    Param("cursor", description="Opaque cursor from a previous page"),
)
LEAD_FILTERS = (
    Param("state", description="Pipeline stage"),
    Param("service_line"),
    Param("city"),
    Param("segment"),
    Param("min_score", "integer", minimum=0, maximum=100),
)
AUTHED = (401, 403, 429, 503)
ID = "Idempotency-Key"

_PULSE_ROUTES: tuple[tuple[str, str, type[BaseModel], tuple[Param, ...]], ...] = (
    ("market", "90-day market totals", JsonObject, ()),
    ("market/overview", "90-day market totals", JsonObject, ()),
    (
        "unprocessed-signals",
        "Unprocessed signal counts per bucket",
        UnprocessedSignalsResponse,
        (
            Param("granularity", default="week"),
            Param("from"),
            Param("to"),
            Param("city"),
        ),
    ),
    (
        "pipeline-backlog",
        "Active leads and capacity estimate",
        PipelineBacklogResponse,
        (Param("city"),),
    ),
    (
        "source-efficiency",
        "Cost and conversion per source",
        SourceEfficiencyResponse,
        (Param("period", default="30d"), Param("service_line")),
    ),
    (
        "time-in-pipeline",
        "Lead time percentiles per group",
        TimeInPipelineResponse,
        (Param("group_by", default="service_line"),),
    ),
    (
        "top-sources",
        "Ranking of sources by qualified leads",
        TopSourcesResponse,
        (Param("k", "integer", minimum=1, maximum=100, default=10), Param("service_line")),
    ),
    ("ops/health", "Per-source freshness and outbox backlog", JsonObject, ()),
    (
        "territory/opportunity",
        "Territories ranked by opportunity index",
        JsonObject,
        (Param("city"), Param("limit", "integer", minimum=1, maximum=500, default=50)),
    ),
)


def _pulse_routes() -> list[PublicRoute]:
    routes = [
        PublicRoute(
            "GET",
            f"/pulse/{suffix}",
            summary,
            "pulse",
            response=model,
            query=query,
            errors=AUTHED,
            response_headers=("X-As-Of", "Cache-Control"),
        )
        for suffix, summary, model, query in _PULSE_ROUTES
    ]
    # Aliases the gateway resolves without the ``pulse/`` prefix.
    for suffix in ("ops/health", "territory/opportunity"):
        routes.append(
            PublicRoute(
                "GET",
                f"/{suffix}",
                f"Alias of /pulse/{suffix}",
                "pulse",
                response=JsonObject,
                query=next(q for s, _, _, q in _PULSE_ROUTES if s == suffix),
                errors=AUTHED,
                response_headers=("X-As-Of", "Cache-Control"),
            )
        )
    routes.append(
        PublicRoute(
            "GET",
            "/segments",
            "Segment read model (not built yet, answers 501)",
            "pulse",
            response=JsonObject,
            errors=(*AUTHED, 501),
        )
    )
    return routes


def _build() -> tuple[PublicRoute, ...]:
    c = AUTHED
    routes = [
        PublicRoute(
            "POST",
            "/auth/session",
            "Start a session (cookie plus CSRF token)",
            "identity",
            response=JsonObject,
            errors=(401, 403, 429, 501, 503),
            public=True,
        ),
        PublicRoute("GET", "/me", "Current user", "identity", response=JsonObject, errors=c),
        PublicRoute("POST", "/logout", "End the session", "identity", status=204, errors=c),
        PublicRoute(
            "GET",
            "/leads",
            "List leads",
            "leads",
            response=Page,
            query=(*PAGING, *LEAD_FILTERS),
            errors=(*c, 400),
        ),
        PublicRoute(
            "POST",
            "/leads",
            "Create a lead",
            "leads",
            response=JsonObject,
            request=LeadCreate,
            status=201,
            errors=(*c, 409, 422),
            request_headers=(ID,),
        ),
        PublicRoute(
            "GET",
            "/leads/{lead_id}",
            "Read a lead",
            "leads",
            response=JsonObject,
            errors=(*c, 404),
            response_headers=("ETag",),
        ),
        PublicRoute(
            "PATCH",
            "/leads/{lead_id}/status",
            "Move a lead to another pipeline stage",
            "leads",
            response=JsonObject,
            request=LeadStatusPatch,
            errors=(*c, 404, 409, 422),
            response_headers=("ETag",),
        ),
        PublicRoute(
            "POST",
            "/leads/{lead_id}/activities",
            "Log an activity on a lead",
            "leads",
            response=JsonObject,
            request=ActivityCreate,
            status=201,
            errors=(*c, 404, 422),
            request_headers=(ID,),
        ),
        PublicRoute(
            "POST",
            "/leads/{lead_id}/outcomes",
            "Record the outcome of a lead",
            "leads",
            response=JsonObject,
            request=OutcomeCreate,
            status=201,
            errors=(*c, 404, 409, 422),
        ),
        PublicRoute(
            "GET",
            "/leads/{lead_id}/brief",
            "Digest of the lead's company",
            "brief",
            response=BriefResponse,
            errors=(*c, 404, 502, 504),
            response_headers=("X-Brief-Cache",),
        ),
        PublicRoute(
            "GET",
            "/pipeline/kanban",
            "Leads grouped by stage",
            "leads",
            response=JsonObject,
            errors=c,
        ),
        PublicRoute(
            "GET",
            "/companies",
            "List companies",
            "companies",
            response=Page,
            query=PAGING,
            errors=(*c, 400),
        ),
        PublicRoute(
            "POST",
            "/companies/upsert",
            "Create or update a company",
            "companies",
            response=JsonObject,
            request=CompanyUpsert,
            status=201,
            errors=(*c, 422),
        ),
        PublicRoute(
            "POST",
            "/signals",
            "Record a market signal",
            "signals",
            response=JsonObject,
            request=SignalCreate,
            status=201,
            errors=(*c, 422),
        ),
        PublicRoute(
            "GET",
            "/projects",
            "List projects",
            "projects",
            response=Page,
            query=PAGING,
            errors=(*c, 400),
        ),
        PublicRoute(
            "POST",
            "/projects",
            "Convert a lead into a project",
            "projects",
            response=JsonObject,
            request=ProjectCreate,
            status=201,
            errors=(*c, 404, 409, 422),
        ),
        PublicRoute(
            "POST",
            "/imports/{entity}",
            "Upload a CSV for a background import",
            "imports",
            response=ImportJobAccepted,
            status=202,
            request_content="text/csv",
            query=(Param("preserve_blob", "boolean", default=False),),
            errors=(*c, 413, 422),
            request_headers=(ID,),
        ),
        PublicRoute(
            "GET",
            "/jobs/{job_id}",
            "State and progress of an import job",
            "imports",
            response=ImportJobResponse,
            errors=(*c, 404),
        ),
        PublicRoute(
            "GET",
            "/admin/tenant-config",
            "Read the tenant configuration",
            "admin",
            response=TenantConfig,
            errors=(*c, 404),
            response_headers=("ETag",),
        ),
        PublicRoute(
            "PUT",
            "/admin/tenant-config",
            "Replace the tenant configuration (optimistic concurrency)",
            "admin",
            response=TenantConfig,
            request=TenantConfig,
            errors=(*c, 412, 422),
            request_headers=("If-Match",),
            response_headers=("ETag",),
        ),
        PublicRoute(
            "PUT",
            "/projects/{project_id}/validation-inputs",
            "Replace the hand-written validation inputs of a project (admin, strategist)",
            "projects",
            response=JsonObject,
            request=ValidationInputs,
            errors=(*c, 400, 404, 412, 422),
            request_headers=("If-Match",),
            response_headers=("ETag",),
        ),
        PublicRoute(
            "POST",
            "/scores",
            "Recompute the score of a company",
            "scores",
            response=ScoreResponse,
            request=ScoreFeatures,
            errors=(*c, 422),
        ),
        PublicRoute(
            "GET",
            "/scores/{company_id}",
            "Read the latest score of a company",
            "scores",
            response=JsonObject,
            errors=(*c, 404),
        ),
        PublicRoute(
            "GET",
            "/funnel/projects/{project_id}",
            "Funnel checklist state of a project (admin, strategist, analyst)",
            "funnel",
            response=JsonObject,
            errors=(*c, 404),
        ),
        PublicRoute(
            "POST",
            "/funnel/projects/{project_id}/gates/{gate}/decision",
            "Record a funnel gate decision (admin, strategist)",
            "funnel",
            response=JsonObject,
            request=FunnelDecisionRequest,
            errors=(*c, 404, 409, 422),
        ),
        PublicRoute(
            "GET",
            "/validation/projects/{project_id}/{card}",
            "Validation metrics card of a project (admin, strategist, analyst)",
            "validation",
            response=JsonObject,
            errors=(*c, 404),
        ),
        PublicRoute(
            "GET",
            "/validation/meta-ml",
            "Tenant meta-ML validation card (admin, strategist, analyst)",
            "validation",
            response=JsonObject,
            errors=(*c, 404),
        ),
        *_pulse_routes(),
    ]
    return tuple(routes)


PUBLIC_ROUTES: tuple[PublicRoute, ...] = _build()
