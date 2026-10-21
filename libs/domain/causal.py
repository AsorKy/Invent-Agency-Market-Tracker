"""Causal inference estimators (REQ-LNS-006, Dx2), pure standard library.

Three estimators share one result type:

* ``difference_in_differences`` - unit-level panel, ATT identified.
* ``synthetic_control`` - one treated series, donor pool, simplex weights.
* ``propensity_matching`` - logistic propensity, 1-NN matching with caliper.

Every result carries ATE with a 95% interval, ATT, ATU (None when the method
does not identify it), a two-sided p-value, an effect size (Cohen's d or
equivalent), robustness checks and an E-value. If the sign of the estimate
flips under any robustness check, status is ``inconclusive``.

E-value (VanderWeele and Ding, 2017): the standardized effect d is converted
to an approximate risk ratio RR = exp(0.91 * d); for RR < 1 use 1 / RR; then
E = RR + sqrt(RR * (RR - 1)). E = 1 when there is no effect.

All solvers use fixed iteration caps and no randomness, so output is
deterministic.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from statistics import fmean, median, pstdev, stdev

Z95 = 1.959963984540054
_MAX_ITER = 500
_SC_ITER = 2000


@dataclass(frozen=True)
class RobustnessCheck:
    """One alternative specification and its estimate."""

    name: str
    estimate: float
    flips_sign: bool


@dataclass(frozen=True)
class CausalResult:
    """Common result of all causal estimators."""

    method: str
    ate: float
    ci_low: float
    ci_high: float
    att: float | None
    atu: float | None
    p_value: float
    effect_size: float
    e_value: float
    robustness: tuple[RobustnessCheck, ...] = field(default_factory=tuple)
    status: str = "ok"
    notes: tuple[str, ...] = field(default_factory=tuple)


def normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def p_value_from_z(z: float) -> float:
    """Two-sided p-value for a z statistic."""
    return min(1.0, 2.0 * (1.0 - normal_cdf(abs(z))))


def e_value(effect_size: float) -> float:
    """E-value from a standardized effect via RR = exp(0.91 * d)."""
    rr = math.exp(0.91 * abs(effect_size))
    if rr <= 1.0:
        return 1.0
    return rr + math.sqrt(rr * (rr - 1.0))


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _checks(main: float, alts: list[tuple[str, float]]) -> tuple[RobustnessCheck, ...]:
    return tuple(
        RobustnessCheck(n, v, _sign(v) != 0 and _sign(main) != 0 and _sign(v) != _sign(main))
        for n, v in alts
    )


def _finish(
    method: str,
    ate: float,
    se: float,
    att: float | None,
    atu: float | None,
    sd: float,
    alts: list[tuple[str, float]],
    notes: list[str],
    p_override: float | None = None,
    ci: tuple[float, float] | None = None,
) -> CausalResult:
    d = ate / sd if sd > 0 else 0.0
    p = (
        p_override
        if p_override is not None
        else (p_value_from_z(ate / se) if se > 0 else 0.0 if ate else 1.0)
    )
    low, high = ci if ci is not None else (ate - Z95 * se, ate + Z95 * se)
    checks = _checks(ate, alts)
    status = "inconclusive" if any(c.flips_sign for c in checks) else "ok"
    return CausalResult(
        method, ate, low, high, att, atu, p, d, e_value(d), checks, status, tuple(notes)
    )


def _welch(a: list[float], b: list[float]) -> tuple[float, float]:
    """Mean difference a-b and its standard error."""
    va = stdev(a) ** 2 / len(a) if len(a) > 1 else 0.0
    vb = stdev(b) ** 2 / len(b) if len(b) > 1 else 0.0
    return fmean(a) - fmean(b), math.sqrt(va + vb)


def _pooled_sd(a: list[float], b: list[float]) -> float:
    na, nb = len(a), len(b)
    if na + nb < 3 or na < 1 or nb < 1:
        return 0.0
    va = stdev(a) ** 2 if na > 1 else 0.0
    vb = stdev(b) ** 2 if nb > 1 else 0.0
    return math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))


# --------------------------------------------------------------------------
# Difference-in-differences
# --------------------------------------------------------------------------


def _did_diffs(units: list[list[float]], start: int, stop: int, trim: int = 0) -> list[float]:
    out = []
    for u in units:
        pre = u[:start][-trim:] if trim else u[:start]
        out.append(fmean(u[start:stop]) - fmean(pre))
    return out


def difference_in_differences(
    treated: list[list[float]], control: list[list[float]], treat_start: int
) -> CausalResult:
    """DID on unit-by-period panels; periods before ``treat_start`` are pre.

    ATE equals ATT here (only the treated are observed post-treatment, so ATU
    is not identified and is None). Unit-level pre/post mean differences are
    compared with a Welch standard error. Robustness: median-based DID,
    last-pre-period-only baseline, and a placebo at the pre-period midpoint
    (reported, with its estimate, when at least four pre periods exist).
    """
    if not treated or not control:
        raise ValueError("need treated and control units")
    n_periods = len(treated[0])
    if any(len(u) != n_periods for u in treated + control) or not 1 <= treat_start < n_periods:
        raise ValueError("ragged panel or invalid treat_start")
    dt = _did_diffs(treated, treat_start, n_periods)
    dc = _did_diffs(control, treat_start, n_periods)
    ate, se = _welch(dt, dc)
    alts = [
        ("median_did", median(dt) - median(dc)),
        (
            "last_pre_period_baseline",
            fmean(_did_diffs(treated, treat_start, n_periods, 1))
            - fmean(_did_diffs(control, treat_start, n_periods, 1)),
        ),
    ]
    notes = ["ATU not identified by DID; ATE equals ATT"]
    if treat_start >= 4:
        mid = treat_start // 2
        pt = [fmean(u[mid:treat_start]) - fmean(u[:mid]) for u in treated]
        pc = [fmean(u[mid:treat_start]) - fmean(u[:mid]) for u in control]
        pe, pse = _welch(pt, pc)
        notes.append(
            f"placebo_pre_period estimate={pe:.6g} p={p_value_from_z(pe / pse) if pse else 1.0:.4g}"
        )
    return _finish("did", ate, se, ate, None, _pooled_sd(dt, dc), alts, notes)


# --------------------------------------------------------------------------
# Synthetic control
# --------------------------------------------------------------------------


def _project_simplex(v: list[float]) -> list[float]:
    u = sorted(v, reverse=True)
    css = 0.0
    theta = 0.0
    for i, x in enumerate(u, start=1):
        css += x
        t = (css - 1.0) / i
        if x - t > 0:
            theta = t
    return [max(x - theta, 0.0) for x in v]


def synthetic_weights(target: list[float], donors: list[list[float]]) -> list[float]:
    """Non-negative weights summing to 1 minimising pre-period squared error.

    Projected gradient descent from uniform weights, fixed iteration cap and
    step 1 / Lipschitz constant, so the result is deterministic.
    """
    k, t = len(donors), len(target)
    if k == 0 or t == 0:
        raise ValueError("need donors and pre-period data")
    lip = max(sum(d[i] ** 2 for i in range(t)) for d in donors) * k or 1.0
    step = 1.0 / (2.0 * lip)
    w = [1.0 / k] * k
    for _ in range(_SC_ITER):
        resid = [sum(w[j] * donors[j][i] for j in range(k)) - target[i] for i in range(t)]
        grad = [2.0 * sum(donors[j][i] * resid[i] for i in range(t)) for j in range(k)]
        w = _project_simplex([w[j] - step * grad[j] for j in range(k)])
    return w


def _sc_gap(
    target: list[float], donors: list[list[float]], pre: int, first_pre: int = 0
) -> tuple[list[float], list[float]]:
    w = synthetic_weights(target[first_pre:pre], [d[first_pre:pre] for d in donors])
    gaps = [
        target[i] - sum(w[j] * donors[j][i] for j in range(len(donors))) for i in range(len(target))
    ]
    return w, gaps


def synthetic_control(
    treated: list[float], donors: list[list[float]], treat_start: int
) -> CausalResult:
    """Synthetic control for one treated series against a donor pool.

    ATE = ATT = mean post-period gap; ATU is None (a single treated unit).
    Inference is placebo-in-space: the method is re-run on each donor against
    the remaining donors; p = share of |placebo gap| >= |treated gap| (treated
    included) and the interval is ATE +- 1.96 * sd of placebo gaps.
    Robustness: shorter pre window, and dropping the heaviest donor.
    """
    n = len(treated)
    if len(donors) < 3 or any(len(d) != n for d in donors) or not 2 <= treat_start < n:
        raise ValueError("need >= 3 donors of equal length and treat_start in [2, n)")
    w, gaps = _sc_gap(treated, donors, treat_start)
    post = gaps[treat_start:]
    ate = fmean(post)
    placebo = []
    for j in range(len(donors)):
        rest = donors[:j] + donors[j + 1 :]
        _, g = _sc_gap(donors[j], rest, treat_start)
        placebo.append(fmean(g[treat_start:]))
    p = (1 + sum(1 for x in placebo if abs(x) >= abs(ate))) / (1 + len(placebo))
    sd_pl = pstdev(placebo) if len(placebo) > 1 else 0.0
    pre_sd = pstdev(treated[:treat_start])
    big = max(range(len(w)), key=lambda j: w[j])
    rest = donors[:big] + donors[big + 1 :]
    alts = [
        ("drop_heaviest_donor", fmean(_sc_gap(treated, rest, treat_start)[1][treat_start:])),
        (
            "short_pre_window",
            fmean(_sc_gap(treated, donors, treat_start, treat_start // 2)[1][treat_start:]),
        ),
    ]
    notes = [
        "ATU not identified: single treated unit",
        "weights=" + ",".join(f"{x:.4f}" for x in w),
    ]
    return _finish(
        "synthetic_control",
        ate,
        sd_pl,
        ate,
        None,
        pre_sd,
        alts,
        notes,
        p_override=p,
        ci=(ate - Z95 * sd_pl, ate + Z95 * sd_pl),
    )


# --------------------------------------------------------------------------
# Propensity matching
# --------------------------------------------------------------------------


def fit_propensity(x: list[list[float]], t: list[int]) -> list[float]:
    """Logit of the fitted propensity score (L2-regularised logistic fit).

    Covariates are standardised; batch gradient descent with a fixed cap of
    500 iterations. Returns the linear predictor per unit.
    """
    n, k = len(x), len(x[0])
    mu = [fmean(r[j] for r in x) for j in range(k)]
    sd = [pstdev([r[j] for r in x]) or 1.0 for j in range(k)]
    z = [[(r[j] - mu[j]) / sd[j] for j in range(k)] + [1.0] for r in x]
    beta = [0.0] * (k + 1)
    lr, lam = 0.5, 1e-3
    for _ in range(_MAX_ITER):
        grad = [lam * beta[j] if j < k else 0.0 for j in range(k + 1)]
        for row, ti in zip(z, t, strict=True):
            eta = max(-30.0, min(30.0, sum(b * v for b, v in zip(beta, row, strict=True))))
            err = 1.0 / (1.0 + math.exp(-eta)) - ti
            for j in range(k + 1):
                grad[j] += err * row[j] / n
        beta = [b - lr * g for b, g in zip(beta, grad, strict=True)]
    return [sum(b * v for b, v in zip(beta, row, strict=True)) for row in z]


def _match(
    scores: list[float], from_idx: list[int], to_idx: list[int], caliper: float | None
) -> dict[int, int]:
    """Nearest neighbour (with replacement) of each ``from`` unit among ``to``."""
    pool = sorted((scores[i], i) for i in to_idx)
    keys = [p[0] for p in pool]
    out: dict[int, int] = {}
    for i in from_idx:
        pos = bisect_left(keys, scores[i])
        cands = [c for c in (pos - 1, pos) if 0 <= c < len(pool)]
        best = min(cands, key=lambda c: (abs(keys[c] - scores[i]), pool[c][1]))
        if caliper is None or abs(keys[best] - scores[i]) <= caliper:
            out[i] = pool[best][1]
    return out


def _psm_effects(
    y: list[float], scores: list[float], tr: list[int], co: list[int], caliper: float | None
) -> tuple[float, float, float, list[float]]:
    mt = _match(scores, tr, co, caliper)
    mc = _match(scores, co, tr, caliper)
    eff_t = [y[i] - y[j] for i, j in mt.items()]
    eff_c = [y[j] - y[i] for i, j in mc.items()]
    if not eff_t or not eff_c:
        raise ValueError("no matches within caliper")
    att, atu = fmean(eff_t), fmean(eff_c)
    ate = (att * len(eff_t) + atu * len(eff_c)) / (len(eff_t) + len(eff_c))
    return ate, att, atu, eff_t + eff_c


def propensity_matching(
    x: list[list[float]], t: list[int], y: list[float], caliper_sd: float = 0.2
) -> CausalResult:
    """Propensity-score matching, 1-NN with replacement and a logit caliper.

    The caliper is ``caliper_sd`` times the sd of the logit score. ATT matches
    treated to controls, ATU matches controls to treated, and ATE is their
    match-count weighted mean. The standard error is the sd of the matched
    unit effects over sqrt(n), a simple approximation that ignores matching
    uncertainty. Robustness: caliper halved, caliper doubled, no caliper.
    """
    n = len(y)
    if n != len(x) or n != len(t) or not x:
        raise ValueError("x, t and y must have equal non-zero length")
    tr = [i for i in range(n) if t[i] == 1]
    co = [i for i in range(n) if t[i] == 0]
    if len(tr) < 2 or len(co) < 2:
        raise ValueError("need at least two treated and two control units")
    scores = fit_propensity(x, t)
    sd_s = pstdev(scores)
    ate, att, atu, effs = _psm_effects(y, scores, tr, co, caliper_sd * sd_s)
    se = stdev(effs) / math.sqrt(len(effs))
    alts = []
    for name, c in (
        ("caliper_half", caliper_sd * sd_s / 2),
        ("caliper_double", caliper_sd * sd_s * 2),
        ("no_caliper", None),
    ):
        try:
            alts.append((name, _psm_effects(y, scores, tr, co, c)[0]))
        except ValueError:
            continue
    sd_y = _pooled_sd([y[i] for i in tr], [y[i] for i in co])
    return _finish("propensity_matching", ate, se, att, atu, sd_y, alts, [f"matched={len(effs)}"])
