"""Define the analytical lens tags and the quality metrics each lens must publish.

The module holds only an enum, constants, and pure functions so it adds no Pydantic
model to the frozen contract snapshot. It relates to REQ-LNS-001 and encodes
ADR-IMT-0002 sections 2 and 2.9: every analytical endpoint or worker declares one
lens and reports that lens' quality metrics. Descriptive reports freshness and
completeness, diagnostic reports ATE with a confidence interval, p-value, and effect
size, predictive reports AUC, calibration, and lift. The prescriptive and validation
lenses name the gate and the project metric with its kill trigger (ADR section 2.9).
"""

from enum import StrEnum


class Lens(StrEnum):
    DESCRIPTIVE = "D"
    DIAGNOSTIC = "Dx"
    PREDICTIVE = "P"
    PRESCRIPTIVE = "prescriptive"
    VALIDATION = "V"


REQUIRED_METRICS: dict[Lens, tuple[str, ...]] = {
    Lens.DESCRIPTIVE: ("freshness", "completeness"),
    Lens.DIAGNOSTIC: ("ate", "ci_low", "ci_high", "p_value", "effect_size"),
    Lens.PREDICTIVE: ("auc", "calibration", "lift"),
    Lens.PRESCRIPTIVE: ("gate",),
    Lens.VALIDATION: ("project_metric", "kill_trigger"),
}


def required_metrics(lens: Lens | str) -> tuple[str, ...]:
    """Return the metric names the lens must publish; raise ValueError for unknown lenses."""
    return REQUIRED_METRICS[Lens(lens)]


def missing_metrics(lens: Lens | str, reported: dict[str, object]) -> list[str]:
    """Return required metric names absent from ``reported`` or reported as None."""
    return [name for name in required_metrics(lens) if reported.get(name) is None]


def is_complete(lens: Lens | str, reported: dict[str, object]) -> bool:
    """Return True when ``reported`` carries every metric the lens requires."""
    return not missing_metrics(lens, reported)
