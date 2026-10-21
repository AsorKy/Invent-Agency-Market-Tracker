"""Compute D1 descriptive statistics and project descriptive metrics.

Pure functions only (REQ-LNS-003, ADR-IMT-0002 section 2.1): percentiles
p10/p50/p90, MAD, IQR, HHI, and Gini, plus the project metrics (pipeline size,
score distribution, burn versus plan, pipeline value by service line, median
time per funnel stage) over plain row inputs. Empty input yields ``None``, never
a fake zero. The module relates to ``models.py`` and ``main.py``, which expose
the results as additive response fields, and performs no I/O.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from services.pulse.app.models import (
    BurnVersusPlan,
    Concentration,
    DescriptiveStats,
    ServiceLineValue,
    StageDuration,
)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Return the q-th percentile (0..100) with linear interpolation, or None if empty."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def median(values: Sequence[float]) -> float | None:
    """Return the median, or None when there are no values."""
    return percentile(values, 50)


def mad(values: Sequence[float]) -> float | None:
    """Return the median absolute deviation around the median (unscaled)."""
    centre = median(values)
    if centre is None:
        return None
    return median([abs(v - centre) for v in values])


def iqr(values: Sequence[float]) -> float | None:
    """Return the interquartile range p75 - p25, or None when empty."""
    q1, q3 = percentile(values, 25), percentile(values, 75)
    if q1 is None or q3 is None:
        return None
    return q3 - q1


def hhi(values: Sequence[float]) -> float | None:
    """Return the Herfindahl-Hirschman index on shares (1/n .. 1), or None if total is 0."""
    total = sum(values)
    if not values or total <= 0:
        return None
    return sum((v / total) ** 2 for v in values)


def gini(values: Sequence[float]) -> float | None:
    """Return the Gini coefficient (0 equal .. (n-1)/n concentrated), or None if total is 0."""
    total = sum(values)
    if not values or total <= 0 or min(values) < 0:
        return None
    ordered = sorted(values)
    n = len(ordered)
    weighted = sum((rank + 1) * v for rank, v in enumerate(ordered))
    return (2 * weighted) / (n * total) - (n + 1) / n


def describe(values: Sequence[float]) -> DescriptiveStats:
    """Return count, p10/p50/p90, MAD, and IQR for a sample."""
    return DescriptiveStats(
        n=len(values),
        p10=percentile(values, 10),
        p50=percentile(values, 50),
        p90=percentile(values, 90),
        mad=mad(values),
        iqr=iqr(values),
    )


def concentration(values: Sequence[float]) -> Concentration:
    """Return HHI and Gini for non-negative magnitudes."""
    return Concentration(n=len(values), hhi=hhi(values), gini=gini(values))


@dataclass(frozen=True)
class ProjectRow:
    """Plain project input; money and burn are optional because they may be unknown."""

    project_id: str
    stage: str
    service_line: str
    score: float | None = None
    value: float | None = None
    planned_burn: float | None = None
    actual_burn: float | None = None


@dataclass(frozen=True)
class StageVisit:
    """One stay of a project in a funnel stage; ``exited_at`` is None while open."""

    project_id: str
    stage: str
    entered_at: datetime
    exited_at: datetime | None


def pipeline_size(projects: Iterable[ProjectRow]) -> dict[str, int]:
    """Count projects per stage, adding the ``total`` key."""
    counts: dict[str, int] = defaultdict(int)
    total = 0
    for project in projects:
        counts[project.stage] += 1
        total += 1
    return {**dict(sorted(counts.items())), "total": total}


def score_distribution(projects: Iterable[ProjectRow]) -> DescriptiveStats:
    """Describe the project scores, ignoring projects without a score."""
    return describe([p.score for p in projects if p.score is not None])


def burn_versus_plan(projects: Iterable[ProjectRow]) -> BurnVersusPlan:
    """Sum planned and actual burn over projects that report both; ratio is None if plan is 0."""
    pairs = [
        (p.planned_burn, p.actual_burn)
        for p in projects
        if p.planned_burn is not None and p.actual_burn is not None
    ]
    if not pairs:
        return BurnVersusPlan(n=0)
    planned = sum(p for p, _ in pairs)
    actual = sum(a for _, a in pairs)
    return BurnVersusPlan(
        n=len(pairs),
        planned=planned,
        actual=actual,
        variance=actual - planned,
        ratio=actual / planned if planned > 0 else None,
    )


def pipeline_value_by_service_line(projects: Iterable[ProjectRow]) -> list[ServiceLineValue]:
    """Sum project value per service line with each share of the total, sorted by line."""
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for p in projects:
        if p.value is None:
            continue
        totals[p.service_line] += p.value
        counts[p.service_line] += 1
    grand = sum(totals.values())
    return [
        ServiceLineValue(
            service_line=line,
            value=totals[line],
            projects=counts[line],
            share=totals[line] / grand if grand > 0 else None,
        )
        for line in sorted(totals)
    ]


def median_days_per_stage(visits: Iterable[StageVisit]) -> list[StageDuration]:
    """Return the median closed-visit duration in days per stage; open visits are ignored."""
    durations: dict[str, list[float]] = defaultdict(list)
    for visit in visits:
        if visit.exited_at is None:
            continue
        days = (visit.exited_at - visit.entered_at).total_seconds() / 86400
        if days >= 0:
            durations[visit.stage].append(days)
    return [
        StageDuration(stage=stage, median_days=median(durations[stage]), n=len(durations[stage]))
        for stage in sorted(durations)
    ]
