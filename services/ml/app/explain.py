"""Explainability payload for the Brief (Dx4, REQ-LNS-007).

Pure pieces: linear per-feature contributions ``weight * (x - baseline)`` and
a bounded greedy counterfactual search that finds the smallest feature moves
that flip a score across a threshold. SHAP is optional and loaded lazily; a
missing package raises ``MissingDependency``. ``build_explanation`` returns the
payload that ``services/brief`` should attach on regeneration; that wiring is
outside this task. It relates to ``libs/domain/scoring.py`` features.
"""

import importlib
from collections.abc import Callable, Mapping
from typing import Any


class MissingDependency(RuntimeError):
    """Raised when an optional explainability package is not installed."""


def load_shap() -> Any:
    """Import ``shap`` lazily or raise ``MissingDependency``."""
    try:
        return importlib.import_module("shap")
    except ImportError as exc:
        raise MissingDependency("shap is not installed; use linear_contributions") from exc


def linear_contributions(
    weights: Mapping[str, float], features: Mapping[str, float], baseline: Mapping[str, float]
) -> dict[str, float]:
    """Contribution per feature: ``weight * (x - baseline)``; missing baseline is 0."""
    return {
        name: weight * (features.get(name, 0.0) - baseline.get(name, 0.0))
        for name, weight in weights.items()
    }


def top_drivers(contributions: Mapping[str, float], limit: int = 3) -> list[dict[str, Any]]:
    """Largest absolute contributions first, ties by name; at most ``limit`` items."""
    ordered = sorted(contributions.items(), key=lambda item: (-abs(item[1]), item[0]))
    return [
        {"feature": name, "contribution": value, "direction": "up" if value >= 0 else "down"}
        for name, value in ordered[:limit]
    ]


def shap_contributions(
    model_predict: Callable[..., Any], row: list[float], background: Any
) -> list[float]:
    """Kernel SHAP values for one row; needs the optional ``shap`` package."""
    shap = load_shap()
    explainer = shap.KernelExplainer(model_predict, background)
    return [float(v) for v in explainer.shap_values(row)]


def greedy_counterfactual(
    score_fn: Callable[[Mapping[str, float]], float],
    features: Mapping[str, float],
    bounds: Mapping[str, tuple[float, float, float]],
    *,
    threshold: float,
    max_steps: int = 20,
) -> dict[str, Any]:
    """Greedy search for the smallest moves that flip the score across ``threshold``.

    ``bounds`` maps feature to ``(low, high, step)``. Each iteration applies the
    single step (up or down) that moves the score most toward the other side;
    at most ``max_steps`` iterations. Returns ``found``, ``changes`` (feature to
    ``{"from", "to"}``), ``steps`` and the final ``score``.
    """
    current = dict(features)
    start = score_fn(current)
    want_up = start < threshold

    def flipped(value: float) -> bool:
        return value >= threshold if want_up else value < threshold

    score = start
    steps = 0
    while steps < max_steps and not flipped(score):
        best: tuple[float, str, float] | None = None
        for name, (low, high, step) in sorted(bounds.items()):
            for delta in (step, -step):
                moved = min(max(current.get(name, 0.0) + delta, low), high)
                if moved == current.get(name, 0.0):
                    continue
                trial = score_fn({**current, name: moved})
                gain = trial - score if want_up else score - trial
                if gain > 0 and (best is None or gain > best[0]):
                    best = (gain, name, moved)
        if best is None:
            break
        current[best[1]] = best[2]
        score = score_fn(current)
        steps += 1
    changes = {
        name: {"from": features.get(name, 0.0), "to": value}
        for name, value in current.items()
        if value != features.get(name, 0.0)
    }
    return {"found": flipped(score), "changes": changes, "steps": steps, "score": score}


def build_explanation(
    weights: Mapping[str, float],
    features: Mapping[str, float],
    baseline: Mapping[str, float],
    bounds: Mapping[str, tuple[float, float, float]],
    *,
    threshold: float,
    intercept: float = 0.0,
    max_steps: int = 20,
) -> dict[str, Any]:
    """Build the Brief payload for a linear score ``intercept + sum(w*x)``."""

    def score_fn(values: Mapping[str, float]) -> float:
        return intercept + sum(w * values.get(n, 0.0) for n, w in weights.items())

    return {
        "method": "linear",
        "score": score_fn(features),
        "drivers": top_drivers(linear_contributions(weights, features, baseline)),
        "counterfactual": greedy_counterfactual(
            score_fn, features, bounds, threshold=threshold, max_steps=max_steps
        ),
    }
