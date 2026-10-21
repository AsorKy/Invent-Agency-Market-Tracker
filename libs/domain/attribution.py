"""Multi-touch attribution models (REQ-LNS-005, Dx1).

Pure functions over ``Journey`` dataclasses: last-touch, first-touch,
time-decay (exponential half-life), Markov removal-effect, and Shapley. Every
model returns ``{channel: credit}`` and the credit sums to the number of
converted journeys. Shapley is refused unless explicitly enabled and at least
``SHAPLEY_MIN_CLOSES`` labeled closes exist. It relates to
``services/ml/app/attribution_service.py``, which shapes rows for
``pulse.attribution``. It prevents unreliable attribution on thin data.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from math import factorial

SHAPLEY_MIN_CLOSES = 100
SHAPLEY_MAX_CHANNELS = 12
_START, _CONV, _NULL = "(start)", "(conversion)", "(null)"


class AttributionError(ValueError):
    """Raised when a model cannot run on the given journeys or settings."""


@dataclass(frozen=True)
class Touch:
    """One marketing touch: a channel at a timestamp."""

    channel: str
    at: datetime


@dataclass(frozen=True)
class Journey:
    """Ordered touches and whether the journey ended in a close.

    ``converted_at`` defaults to the last touch time for time-decay.
    """

    touches: tuple[Touch, ...]
    converted: bool
    converted_at: datetime | None = None


def count_closes(journeys: Sequence[Journey]) -> int:
    """Return the number of converted journeys."""
    return sum(1 for j in journeys if j.converted)


def _converted(journeys: Sequence[Journey]) -> list[Journey]:
    out = [j for j in journeys if j.converted]
    for j in out:
        if not j.touches:
            raise AttributionError("a converted journey needs at least one touch")
    return out


def last_touch(journeys: Sequence[Journey]) -> dict[str, float]:
    """Give each close fully to the chronologically last touch."""
    credit: dict[str, float] = defaultdict(float)
    for j in _converted(journeys):
        credit[max(j.touches, key=lambda t: t.at).channel] += 1.0
    return dict(credit)


def first_touch(journeys: Sequence[Journey]) -> dict[str, float]:
    """Give each close fully to the chronologically first touch."""
    credit: dict[str, float] = defaultdict(float)
    for j in _converted(journeys):
        credit[min(j.touches, key=lambda t: t.at).channel] += 1.0
    return dict(credit)


def time_decay(journeys: Sequence[Journey], half_life_days: float = 7.0) -> dict[str, float]:
    """Split each close by weights ``0.5 ** (age / half_life)``, normalised to 1."""
    if half_life_days <= 0:
        raise AttributionError("half_life_days must be positive")
    credit: dict[str, float] = defaultdict(float)
    for j in _converted(journeys):
        end = j.converted_at or max(t.at for t in j.touches)
        weights = [
            0.5 ** (max((end - t.at).total_seconds(), 0.0) / 86400.0 / half_life_days)
            for t in j.touches
        ]
        total = sum(weights)
        for t, w in zip(j.touches, weights, strict=True):
            credit[t.channel] += w / total
    return dict(credit)


def _conversion_probability(
    trans: dict[str, dict[str, float]], removed: str | None, states: list[str]
) -> float:
    """Absorption probability in conversion from start, via Gauss-Jordan."""
    idx = {s: i for i, s in enumerate(states)}
    n = len(states)
    # (I - Q) x = r, where r is the one-step probability into conversion.
    a = [[0.0] * (n + 1) for _ in range(n)]
    for s, i in idx.items():
        a[i][i] = 1.0
        if s == removed:
            continue  # removed channel leads to null: x = 0
        for nxt, p in trans.get(s, {}).items():
            if nxt == _CONV:
                a[i][n] += p
            elif nxt in idx and nxt != removed:
                a[i][idx[nxt]] -= p
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise AttributionError("singular Markov chain (a loop never absorbs)")
        a[col], a[piv] = a[piv], a[col]
        div = a[col][col]
        a[col] = [v / div for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col], strict=True)]
    return a[idx[_START]][n]


def markov(journeys: Sequence[Journey]) -> dict[str, float]:
    """Markov removal-effect credit, scaled so the total equals the close count."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    closes = 0
    for j in journeys:
        if not j.touches:
            continue
        path = [_START, *(t.channel for t in sorted(j.touches, key=lambda t: t.at))]
        path.append(_CONV if j.converted else _NULL)
        closes += 1 if j.converted else 0
        for a, b in zip(path, path[1:], strict=False):
            counts[a][b] += 1
    if closes == 0:
        return {}
    trans = {a: {b: c / sum(nb.values()) for b, c in nb.items()} for a, nb in counts.items()}
    channels = sorted(c for c in trans if c != _START)
    states = [_START, *channels]
    base = _conversion_probability(trans, None, states)
    effects = {c: 1.0 - _conversion_probability(trans, c, states) / base for c in channels}
    total = sum(effects.values())
    return {c: e / total * closes for c, e in effects.items() if e > 0}


def shapley(
    journeys: Sequence[Journey], *, enabled: bool = False, min_closes: int = SHAPLEY_MIN_CLOSES
) -> dict[str, float]:
    """Exact Shapley credit; refused unless enabled and closes >= 100.

    The value of a channel set S is the number of closes whose journeys use
    only channels in S, so credit sums to the total closes.
    """
    if not enabled:
        raise AttributionError("shapley attribution is disabled; enable it explicitly")
    closes = count_closes(journeys)
    if closes < max(min_closes, SHAPLEY_MIN_CLOSES):
        raise AttributionError(
            f"shapley attribution needs {SHAPLEY_MIN_CLOSES} labeled closes, got {closes}"
        )
    sets = [frozenset(t.channel for t in j.touches) for j in _converted(journeys)]
    channels = sorted(set().union(*sets))
    n = len(channels)
    if n > SHAPLEY_MAX_CHANNELS:
        raise AttributionError(f"shapley supports at most {SHAPLEY_MAX_CHANNELS} channels")
    value: dict[frozenset[str], int] = {}

    def v(s: frozenset[str]) -> int:
        if s not in value:
            value[s] = sum(1 for js in sets if js <= s)
        return value[s]

    credit = dict.fromkeys(channels, 0.0)
    for c in channels:
        others = [x for x in channels if x != c]
        for k in range(n):
            w = factorial(k) * factorial(n - k - 1) / factorial(n)
            for sub in combinations(others, k):
                s = frozenset(sub)
                credit[c] += w * (v(s | {c}) - v(s))
    return credit
