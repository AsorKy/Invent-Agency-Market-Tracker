"""Test the D1 descriptive statistics and project metrics (REQ-LNS-003).

Expected values are computed by hand in the comments. No I/O is involved.
"""

from datetime import UTC, datetime

import pytest

from services.pulse.app import descriptive as d
from services.pulse.app.descriptive import ProjectRow, StageVisit


def test_percentiles_linear_interpolation() -> None:
    values = [1, 2, 3, 4, 5]
    # position = 4 * q / 100: p10 -> 0.4 -> 1.4; p50 -> 3; p90 -> 3.6 -> 4.6
    assert d.percentile(values, 10) == pytest.approx(1.4)
    assert d.percentile(values, 50) == 3
    assert d.percentile(values, 90) == pytest.approx(4.6)
    assert d.percentile([7], 90) == 7
    assert d.percentile([], 50) is None
    with pytest.raises(ValueError):
        d.percentile(values, 101)


def test_mad_and_iqr() -> None:
    values = [1, 2, 3, 4, 100]
    # median 3; deviations 2,1,0,1,97 -> sorted 0,1,1,2,97 -> MAD 1
    assert d.mad(values) == 1
    # p25 = 2, p75 = 4 -> IQR 2
    assert d.iqr(values) == 2
    assert d.mad([]) is None
    assert d.iqr([]) is None


def test_hhi() -> None:
    # shares .5,.3,.2 -> .25+.09+.04 = .38
    assert d.hhi([50, 30, 20]) == pytest.approx(0.38)
    assert d.hhi([10]) == 1
    assert d.hhi([1, 1, 1, 1]) == pytest.approx(0.25)
    assert d.hhi([]) is None
    assert d.hhi([0, 0]) is None


def test_gini() -> None:
    assert d.gini([5, 5, 5, 5]) == pytest.approx(0)
    # sorted 1,2,3,4 total 10: 2*(1+4+9+16)/(4*10) - 5/4 = 1.5 - 1.25 = 0.25
    assert d.gini([4, 1, 3, 2]) == pytest.approx(0.25)
    # one holder of four: 2*4*10/(4*10) - 1.25 = 0.75
    assert d.gini([0, 0, 0, 10]) == pytest.approx(0.75)
    assert d.gini([]) is None
    assert d.gini([0, 0]) is None
    assert d.gini([-1, 2]) is None


def test_describe_and_concentration_models() -> None:
    stats = d.describe([1, 2, 3, 4, 5])
    assert (stats.n, stats.p50, stats.iqr, stats.mad) == (5, 3, 2, 1)
    assert d.describe([]).p50 is None
    conc = d.concentration([50, 30, 20])
    assert conc.hhi == pytest.approx(0.38)
    assert d.concentration([]).gini is None


PROJECTS = [
    ProjectRow("a", "qualified", "web", score=0.2, value=100, planned_burn=10, actual_burn=12),
    ProjectRow("b", "qualified", "web", score=0.8, value=50, planned_burn=20, actual_burn=18),
    ProjectRow("c", "proposal", "data", score=None, value=50, planned_burn=None, actual_burn=5),
]


def test_pipeline_size() -> None:
    assert d.pipeline_size(PROJECTS) == {"proposal": 1, "qualified": 2, "total": 3}
    assert d.pipeline_size([]) == {"total": 0}


def test_score_distribution_ignores_missing() -> None:
    stats = d.score_distribution(PROJECTS)
    assert stats.n == 2
    assert stats.p50 == pytest.approx(0.5)


def test_burn_versus_plan() -> None:
    burn = d.burn_versus_plan(PROJECTS)
    # only a and b report both: planned 30, actual 30
    assert (burn.n, burn.planned, burn.actual, burn.variance, burn.ratio) == (2, 30, 30, 0, 1.0)
    empty = d.burn_versus_plan([])
    assert empty.n == 0 and empty.ratio is None
    zero = d.burn_versus_plan([ProjectRow("z", "s", "l", planned_burn=0, actual_burn=3)])
    assert zero.ratio is None and zero.variance == 3


def test_value_by_service_line() -> None:
    rows = d.pipeline_value_by_service_line(PROJECTS)
    assert [(r.service_line, r.value, r.projects) for r in rows] == [
        ("data", 50, 1),
        ("web", 150, 2),
    ]
    assert rows[1].share == pytest.approx(0.75)
    assert d.pipeline_value_by_service_line([]) == []


def test_median_days_per_stage() -> None:
    def at(day: int) -> datetime:
        return datetime(2026, 1, day, tzinfo=UTC)

    visits = [
        StageVisit("a", "qualified", at(1), at(3)),  # 2 days
        StageVisit("b", "qualified", at(1), at(5)),  # 4 days
        StageVisit("c", "qualified", at(1), at(11)),  # 10 days
        StageVisit("d", "qualified", at(1), None),  # open, ignored
        StageVisit("a", "proposal", at(3), at(4)),  # 1 day
    ]
    rows = d.median_days_per_stage(visits)
    assert [(r.stage, r.median_days, r.n) for r in rows] == [
        ("proposal", 1, 1),
        ("qualified", 4, 3),
    ]
    assert d.median_days_per_stage([]) == []
