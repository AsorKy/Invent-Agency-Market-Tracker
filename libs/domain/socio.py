"""Compute the socioeconomic indices IPET, IMD, and IOP (REQ-SOC-001).

The module is pure Python and has no I/O. It relates to the Pulse socio loader
in ``services/pulse/app/socio_loader.py`` and the opportunity endpoint in
``services/pulse/app/main.py``. Indicators are standardized with z-scores
within each city, combined with entropy weights (no arbitrary weights, ADR-IMT-0001
section 11.2), and scaled to 0..1 inside the city. IOP combines high economic
power, low digital maturity, and prospect density. Spatial autocorrelation is L3.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

METHOD_VERSION = "socio-entropy-v1"
LOW_SAMPLE_BELOW = 5
IPET_FIELDS = ("gdp_per_capita", "formality_rate", "youth_employment_rate", "business_growth_rate")
IMD_FIELDS = ("web_share", "ecommerce_share", "app_share")
DENSITY_FIELD = "prospect_density"
INDICATOR_FIELDS = (*IPET_FIELDS, *IMD_FIELDS, DENSITY_FIELD)
_EPSILON = 1e-12


@dataclass(frozen=True)
class TerritoryRecord:
    """One territory with its raw indicator values."""

    city: str
    territory_code: str
    territory_name: str
    indicators: dict[str, float]


@dataclass(frozen=True)
class TerritoryIndex:
    """Computed indices of one territory, each on a 0..1 scale within its city."""

    city: str
    territory_code: str
    territory_name: str
    ipet: float
    imd: float
    iop: float
    z_scores: dict[str, float]
    n_in_city: int
    low_sample: bool
    rank: int = 0


def z_scores(values: Sequence[float]) -> list[float]:
    """Return population z-scores; a constant or single-value series gives zeros."""
    if not values:
        return []
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance)
    if std < _EPSILON:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


def entropy_weights(columns: Sequence[Sequence[float]]) -> list[float]:
    """Return entropy-method weights for criteria columns (one list per criterion).

    Each column is min-max shifted to a positive range and normalized to a
    distribution. Criteria with more dispersion get more weight. When no
    criterion varies (or there is a single row), the weights are equal.
    """
    count = len(columns)
    if count == 0:
        return []
    rows = len(columns[0])
    equal = [1.0 / count] * count
    if rows < 2:
        return equal
    divergences: list[float] = []
    for column in columns:
        low, high = min(column), max(column)
        span = high - low
        shifted = [((v - low) / span if span > _EPSILON else 0.0) + 1e-9 for v in column]
        total = sum(shifted)
        entropy = -sum((p / total) * math.log(p / total) for p in shifted) / math.log(rows)
        divergences.append(max(0.0, 1.0 - entropy))
    total_div = sum(divergences)
    if total_div < _EPSILON:
        return equal
    return [d / total_div for d in divergences]


def _scale(values: Sequence[float]) -> list[float]:
    """Min-max scale to 0..1; a constant series maps to 0.5."""
    low, high = min(values), max(values)
    if high - low < _EPSILON:
        return [0.5] * len(values)
    return [(v - low) / (high - low) for v in values]


def _composite(zmap: dict[str, list[float]], fields: Sequence[str]) -> list[float]:
    """Weight the z-score columns of ``fields`` with entropy weights and sum them."""
    columns = [zmap[f] for f in fields]
    weights = entropy_weights(columns)
    size = len(columns[0])
    return [sum(w * col[i] for w, col in zip(weights, columns, strict=True)) for i in range(size)]


def compute_city(records: Sequence[TerritoryRecord]) -> list[TerritoryIndex]:
    """Compute IPET, IMD, and IOP for the territories of one city (unranked)."""
    if not records:
        return []
    zmap = {f: z_scores([r.indicators[f] for r in records]) for f in INDICATOR_FIELDS}
    ipet = _scale(_composite(zmap, IPET_FIELDS))
    imd = _scale(_composite(zmap, IMD_FIELDS))
    density = _scale(zmap[DENSITY_FIELD])
    low = len(records) < LOW_SAMPLE_BELOW
    result: list[TerritoryIndex] = []
    for i, rec in enumerate(records):
        result.append(
            TerritoryIndex(
                city=rec.city,
                territory_code=rec.territory_code,
                territory_name=rec.territory_name,
                ipet=round(ipet[i], 6),
                imd=round(imd[i], 6),
                iop=round(ipet[i] * (1.0 - imd[i]) * density[i], 6),
                z_scores={f: round(zmap[f][i], 6) for f in INDICATOR_FIELDS},
                n_in_city=len(records),
                low_sample=low,
            )
        )
    return result


def compute_indices(records: Sequence[TerritoryRecord]) -> list[TerritoryIndex]:
    """Compute indices per city and return all territories ranked by IOP descending.

    Ties break on city and territory code so the ranking is deterministic.
    """
    by_city: dict[str, list[TerritoryRecord]] = defaultdict(list)
    for rec in records:
        by_city[rec.city].append(rec)
    computed = [idx for city in sorted(by_city) for idx in compute_city(by_city[city])]
    computed.sort(key=lambda t: (-t.iop, t.city, t.territory_code))
    return [
        TerritoryIndex(**{**t.__dict__, "rank": position})
        for position, t in enumerate(computed, start=1)
    ]
