"""Shared input validation for the metric pack.

Every public metric calls these helpers so that malformed input fails the same
way: with a ``ValueError`` that names the argument. Relates to
``libs/domain/metrics/__init__.py`` (REQ-LNS-008).
"""

from __future__ import annotations

from collections.abc import Sequence


def require_non_empty(name: str, values: Sequence[object]) -> None:
    """Raise ``ValueError`` when ``values`` has no elements."""
    if len(values) == 0:
        raise ValueError(f"{name} must not be empty")


def require_same_length(**arrays: Sequence[object]) -> None:
    """Raise ``ValueError`` when the named sequences differ in length."""
    lengths = {name: len(values) for name, values in arrays.items()}
    if len(set(lengths.values())) > 1:
        detail = ", ".join(f"{name}={size}" for name, size in lengths.items())
        raise ValueError(f"length mismatch: {detail}")


def require_binary(name: str, values: Sequence[int]) -> None:
    """Raise ``ValueError`` unless every value is 0 or 1."""
    for value in values:
        if value not in (0, 1):
            raise ValueError(f"{name} must contain only 0 or 1, got {value!r}")


def require_both_classes(name: str, values: Sequence[int]) -> None:
    """Raise ``ValueError`` unless ``values`` contains both a 0 and a 1."""
    ones = sum(1 for value in values if value == 1)
    if ones == 0 or ones == len(values):
        raise ValueError(f"{name} must contain both classes (0 and 1); got a single class")


def require_probabilities(name: str, values: Sequence[float]) -> None:
    """Raise ``ValueError`` unless every value lies in [0, 1]."""
    for value in values:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1], got {value!r}")


def require_k(k: int, size: int) -> None:
    """Raise ``ValueError`` unless ``k`` is an integer in [1, size]."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1 or k > size:
        raise ValueError(f"k must be an integer in [1, {size}], got {k!r}")


def ranked_order(scores: Sequence[float]) -> list[int]:
    """Return indices ordered by descending score; ties keep input order."""
    return sorted(range(len(scores)), key=lambda index: -scores[index])
