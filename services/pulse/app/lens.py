"""Serve the lens routes of Pulse: ml result ingest, project metrics, socio refresh.

Implements REQ-LNS-003 and REQ-LNS-004. The ingest routes receive the payloads
that ``services/ml/app/publisher.py`` posts (``PATHS``) and upsert them into
the ``pulse`` lens tables (``migrations/20261021_0010_pulse_lens_tables.sql``).
The metrics route reads the tenant-scoped project tables and feeds
``descriptive.py``. The refresh route reloads the DANE indicator files through
``socio_loader.py``. Every query runs in a transaction that sets
``app.tenant_id`` first, so forced row level security applies. The tenant comes
from the ``X-Tenant-Id`` header or the optional ``tenant_id`` body field; when
both are present they must match. The module is wired in ``main.py``.
"""

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from libs.common.internal_auth import require_internal_token, require_service_token
from libs.common.tenant import set_tenant_context
from services.pulse.app.descriptive import (
    ProjectRow,
    StageVisit,
    burn_versus_plan,
    median_days_per_stage,
    pipeline_size,
    pipeline_value_by_service_line,
    score_distribution,
)
from services.pulse.app.models import (
    BurnVersusPlan,
    DescriptiveStats,
    ServiceLineValue,
    StageDuration,
)
from services.pulse.app.socio_loader import SocioDataError, load_records

ML_ONLY = Depends(require_service_token("ml"))
INTERNAL = Depends(require_internal_token)
MAX_ITEMS = 100_000
MAX_TOPICS = 1_000
MAX_ROWS = 50_000

router = APIRouter(prefix="/internal/pulse")


class ProfilesIn(BaseModel):
    """Archetype assignment per company, as published by ml."""

    run_id: str = Field(min_length=1, max_length=200)
    algorithm: str = Field(default="k-prototypes", max_length=100)
    assignments: dict[str, int] = Field(max_length=MAX_ITEMS)
    tenant_id: UUID | None = None


class CommunitiesIn(BaseModel):
    """Community id and centrality per node, as published by ml."""

    run_id: str = Field(min_length=1, max_length=200)
    algorithm: str = Field(default="leiden", max_length=100)
    communities: dict[str, int] = Field(max_length=MAX_ITEMS)
    centrality: dict[str, float] = Field(max_length=MAX_ITEMS)
    tenant_id: UUID | None = None


class TopicsIn(BaseModel):
    """Top terms and LDA topics for a period, as published by ml."""

    run_id: str = Field(min_length=1, max_length=200)
    period: str = Field(min_length=1, max_length=100)
    top_terms: list[list[tuple[str, float]]] = Field(default_factory=list, max_length=MAX_TOPICS)
    lda_topics: list[list[str]] = Field(default_factory=list, max_length=MAX_TOPICS)
    tenant_id: UUID | None = None


class AttributionRowIn(BaseModel):
    """One channel credit of an attribution model."""

    model: Literal["last_touch", "first_touch", "time_decay", "markov", "shapley"]
    channel: str = Field(min_length=1, max_length=200)
    credit: float = Field(allow_inf_nan=False)


class AttributionIn(BaseModel):
    """Attribution rows of one project, as published by ml (REQ-LNS-005)."""

    project_id: UUID
    source_version: str = Field(min_length=1, max_length=200)
    rows: list[AttributionRowIn] = Field(max_length=MAX_TOPICS)
    tenant_id: UUID | None = None


class AttributionResult(BaseModel):
    """Acknowledge an attribution ingest with the number of stored rows."""

    project_id: UUID
    accepted: int = Field(ge=0)


class IngestResult(BaseModel):
    """Acknowledge an ingest call with the number of stored rows."""

    run_id: str
    accepted: int = Field(ge=0)


class ProjectMetricsResponse(BaseModel):
    """Project descriptive metrics of one tenant (REQ-LNS-003)."""

    pipeline_size: dict[str, int]
    score_distribution: DescriptiveStats
    burn_versus_plan: BurnVersusPlan
    value_by_service_line: list[ServiceLineValue]
    median_days_per_stage: list[StageDuration]
    as_of: int


class SocioRefreshIn(BaseModel):
    """Request body of the monthly refresh workflow."""

    source: str = Field(default="dane", pattern=r"^[a-z][a-z0-9_-]{0,31}$")


class SocioRefreshResult(BaseModel):
    """Report how many indicator records were reloaded."""

    status: str
    source: str
    records: int = Field(ge=0)
    as_of: int | None = None


def resolve_tenant(header: UUID | None, body: UUID | None) -> UUID:
    """Return the tenant of a request; refuse missing or conflicting values."""
    if header is None and body is None:
        raise HTTPException(status_code=400, detail="tenant_required")
    if header is not None and body is not None and header != body:
        raise HTTPException(status_code=400, detail="tenant_mismatch")
    tenant = header if header is not None else body
    assert tenant is not None
    return tenant


def _num(value: object) -> float | None:
    """Convert a numeric column to float, keeping NULL as None."""
    return None if value is None else float(value)  # type: ignore[arg-type]


class LensStore:
    """Tenant-scoped SQL access for the lens tables."""

    def __init__(self, engine: AsyncEngine | None) -> None:
        self.engine = engine

    def _engine(self) -> AsyncEngine:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is required for pulse lens routes")
        return self.engine

    async def _write(self, tenant: UUID, statement: TextClause, rows: list[dict[str, Any]]) -> int:
        if rows:
            async with self._engine().begin() as connection:
                await set_tenant_context(connection, tenant)
                await connection.execute(statement, rows)
        return len(rows)

    async def save_profiles(self, tenant: UUID, body: ProfilesIn) -> int:
        """Upsert one archetype per company for a run."""
        rows = [
            {
                "tenant": str(tenant),
                "run": body.run_id,
                "ref": ref,
                "algo": body.algorithm,
                "archetype": archetype,
            }
            for ref, archetype in body.assignments.items()
        ]
        return await self._write(
            tenant,
            text(
                """
            INSERT INTO pulse.company_profile
                (tenant_id, run_id, company_ref, algorithm, archetype)
            VALUES (CAST(:tenant AS uuid), :run, :ref, :algo, :archetype)
            ON CONFLICT (tenant_id, run_id, company_ref)
            DO UPDATE SET algorithm = EXCLUDED.algorithm, archetype = EXCLUDED.archetype
                """
            ),
            rows,
        )

    async def save_communities(self, tenant: UUID, body: CommunitiesIn) -> int:
        """Upsert community and centrality per node for a run."""
        rows = [
            {
                "tenant": str(tenant),
                "run": body.run_id,
                "ref": ref,
                "algo": body.algorithm,
                "community": community,
                "centrality": body.centrality.get(ref),
            }
            for ref, community in body.communities.items()
        ]
        return await self._write(
            tenant,
            text(
                """
            INSERT INTO pulse.company_community
                (tenant_id, run_id, node_ref, algorithm, community, centrality)
            VALUES (CAST(:tenant AS uuid), :run, :ref, :algo, :community, :centrality)
            ON CONFLICT (tenant_id, run_id, node_ref)
            DO UPDATE SET algorithm = EXCLUDED.algorithm,
                          community = EXCLUDED.community,
                          centrality = EXCLUDED.centrality
                """
            ),
            rows,
        )

    async def save_topics(self, tenant: UUID, body: TopicsIn) -> int:
        """Upsert the top-term and LDA topics of a period for a run."""
        rows = [
            {
                "tenant": str(tenant),
                "run": body.run_id,
                "period": body.period,
                "kind": "top_terms",
                "idx": index,
                "terms": json.dumps([[term, weight] for term, weight in terms]),
            }
            for index, terms in enumerate(body.top_terms)
        ] + [
            {
                "tenant": str(tenant),
                "run": body.run_id,
                "period": body.period,
                "kind": "lda",
                "idx": index,
                "terms": json.dumps(terms),
            }
            for index, terms in enumerate(body.lda_topics)
        ]
        return await self._write(
            tenant,
            text(
                """
            INSERT INTO pulse.topic_result
                (tenant_id, run_id, period, kind, topic_index, terms)
            VALUES (CAST(:tenant AS uuid), :run, :period, :kind, :idx,
                    CAST(:terms AS jsonb))
            ON CONFLICT (tenant_id, run_id, period, kind, topic_index)
            DO UPDATE SET terms = EXCLUDED.terms
                """
            ),
            rows,
        )

    async def save_attribution(self, tenant: UUID, body: AttributionIn) -> int:
        """Upsert the channel credits of one project and source version."""
        rows = [
            {
                "tenant": str(tenant),
                "project": str(body.project_id),
                "model": row.model,
                "channel": row.channel,
                "credit": Decimal(str(row.credit)),
                "version": body.source_version,
            }
            for row in body.rows
        ]
        return await self._write(
            tenant,
            text(
                """
            INSERT INTO pulse.attribution
                (tenant_id, project_id, model, channel, credit, source_version)
            VALUES (CAST(:tenant AS uuid), CAST(:project AS uuid), :model, :channel,
                    :credit, :version)
            ON CONFLICT (tenant_id, project_id, model, channel, source_version)
            DO UPDATE SET credit = EXCLUDED.credit, computed_at = now()
                """
            ),
            rows,
        )

    async def project_inputs(
        self, tenant: UUID
    ) -> tuple[list[ProjectRow], list[StageVisit], datetime | None]:
        """Read the tenant's project snapshots and stage visits (bounded)."""
        async with self._engine().begin() as connection:
            await set_tenant_context(connection, tenant)
            projects = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT project_id, stage, service_line, score, pipeline_value,
                                   planned_burn, actual_burn, updated_at
                            FROM pulse.project_snapshot
                            WHERE tenant_id = CAST(:tenant AS uuid)
                            ORDER BY project_id
                            LIMIT :cap
                            """
                        ),
                        {"tenant": str(tenant), "cap": MAX_ROWS},
                    )
                )
                .mappings()
                .all()
            )
            visits = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT project_id, stage, entered_at, exited_at
                            FROM pulse.project_stage_visit
                            WHERE tenant_id = CAST(:tenant AS uuid)
                            ORDER BY project_id, entered_at
                            LIMIT :cap
                            """
                        ),
                        {"tenant": str(tenant), "cap": MAX_ROWS},
                    )
                )
                .mappings()
                .all()
            )
        rows = [
            ProjectRow(
                project_id=str(p["project_id"]),
                stage=p["stage"],
                service_line=p["service_line"],
                score=_num(p["score"]),
                value=_num(p["pipeline_value"]),
                planned_burn=_num(p["planned_burn"]),
                actual_burn=_num(p["actual_burn"]),
            )
            for p in projects
        ]
        stays = [
            StageVisit(str(v["project_id"]), v["stage"], v["entered_at"], v["exited_at"])
            for v in visits
        ]
        as_of = max((p["updated_at"] for p in projects), default=None)
        return rows, stays, as_of


def _store(request: Request) -> LensStore:
    return request.app.state.lens_store  # type: ignore[no-any-return]


TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-Id")]


@router.post("/ingest/profiles", dependencies=[ML_ONLY])
async def ingest_profiles(
    body: ProfilesIn, request: Request, x_tenant_id: TenantHeader = None
) -> IngestResult:
    """Store the archetype assignments published by ml."""
    tenant = resolve_tenant(x_tenant_id, body.tenant_id)
    return IngestResult(
        run_id=body.run_id, accepted=await _store(request).save_profiles(tenant, body)
    )


@router.post("/ingest/communities", dependencies=[ML_ONLY])
async def ingest_communities(
    body: CommunitiesIn, request: Request, x_tenant_id: TenantHeader = None
) -> IngestResult:
    """Store the communities and centrality published by ml."""
    tenant = resolve_tenant(x_tenant_id, body.tenant_id)
    return IngestResult(
        run_id=body.run_id, accepted=await _store(request).save_communities(tenant, body)
    )


@router.post("/ingest/topics", dependencies=[ML_ONLY])
async def ingest_topics(
    body: TopicsIn, request: Request, x_tenant_id: TenantHeader = None
) -> IngestResult:
    """Store the topics published by ml."""
    tenant = resolve_tenant(x_tenant_id, body.tenant_id)
    return IngestResult(
        run_id=body.run_id, accepted=await _store(request).save_topics(tenant, body)
    )


@router.post("/ingest/attribution", dependencies=[ML_ONLY])
async def ingest_attribution(
    body: AttributionIn, request: Request, x_tenant_id: TenantHeader = None
) -> AttributionResult:
    """Store the attribution credits published by ml."""
    tenant = resolve_tenant(x_tenant_id, body.tenant_id)
    return AttributionResult(
        project_id=body.project_id, accepted=await _store(request).save_attribution(tenant, body)
    )


@router.get("/projects/metrics", dependencies=[INTERNAL])
async def project_metrics(
    request: Request, x_tenant_id: TenantHeader = None
) -> ProjectMetricsResponse:
    """Return the project descriptive metrics of the caller's tenant."""
    tenant = resolve_tenant(x_tenant_id, None)
    projects, visits, source_as_of = await _store(request).project_inputs(tenant)
    now = datetime.now().astimezone()
    return ProjectMetricsResponse(
        pipeline_size=pipeline_size(projects),
        score_distribution=score_distribution(projects),
        burn_versus_plan=burn_versus_plan(projects),
        value_by_service_line=pipeline_value_by_service_line(projects),
        median_days_per_stage=median_days_per_stage(visits),
        as_of=int((source_as_of or now).timestamp() * 1000),
    )


@router.post("/socioeconomic/refresh", dependencies=[INTERNAL])
async def socioeconomic_refresh(body: SocioRefreshIn, request: Request) -> SocioRefreshResult:
    """Reload and validate the indicator files; used by the monthly n8n workflow."""
    try:
        records, as_of = await asyncio.to_thread(
            load_records, request.app.state.repository.socio_data_dir
        )
    except SocioDataError as exc:
        raise HTTPException(status_code=503, detail="socio_data_invalid") from exc
    return SocioRefreshResult(
        status="refreshed",
        source=body.source,
        records=len(records),
        as_of=int(as_of.timestamp() * 1000) if as_of is not None else None,
    )
