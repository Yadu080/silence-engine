"""Small, dependency-free statistical helpers (Python standard library only)."""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence, Tuple

MAD_TO_SIGMA = 1.4826  # consistency constant: sigma ~= 1.4826 * MAD for normal data


def median(values: Sequence[float]) -> float:
    """Median of a non-empty sequence."""
    if not values:
        raise ValueError("median() of empty sequence")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: Sequence[float], center: Optional[float] = None) -> float:
    """Median absolute deviation around ``center`` (default: the median)."""
    if not values:
        raise ValueError("mad() of empty sequence")
    c = median(values) if center is None else center
    return median([abs(v - c) for v in values])


def robust_sigma(values: Sequence[float]) -> float:
    """MAD-based estimate of the standard deviation."""
    return MAD_TO_SIGMA * mad(values)


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean() of empty sequence")
    return sum(values) / len(values)


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile, q in [0, 1]."""
    if not values:
        raise ValueError("quantile() of empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def safe_div(num: float, den: float) -> Optional[float]:
    """Division that returns None instead of raising for a zero denominator."""
    if den == 0:
        return None
    return num / den


def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score interval for a binomial proportion (95% by default).

    Returns (None, None) when n == 0 so callers never report a fake interval.
    """
    if n <= 0:
        return (None, None)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(k * math.log(lam) - lam - math.lgamma(k + 1))


def prob_consecutive_equal(lam: float) -> float:
    """P(X == Y) for two independent Poisson(lam) draws.

    Used to choose a flatline run-length that ordinary counting noise is very
    unlikely to produce. Real counts are over-dispersed relative to Poisson, so
    this is a conservative (upper) estimate of the chance of a repeat.
    """
    if lam <= 0:
        return 1.0
    lo = max(0, int(lam - 10 * math.sqrt(lam) - 5))
    hi = int(lam + 10 * math.sqrt(lam) + 10)
    return sum(poisson_pmf(k, lam) ** 2 for k in range(lo, hi + 1))


def flatline_run_length(lam: float, false_alarm: float = 1e-4, lo: int = 3, hi: int = 10) -> int:
    """Smallest run of identical consecutive values with P(run | noise) < false_alarm."""
    p_eq = prob_consecutive_equal(lam)
    if p_eq >= 0.999:
        return hi
    if p_eq <= 0:
        return lo
    # A run of L identical values needs L-1 consecutive repeats.
    needed = math.ceil(math.log(false_alarm) / math.log(p_eq)) + 1
    return max(lo, min(hi, needed))


def round_or_none(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(value, digits)


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def f1(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def summarize(values: Iterable[float]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "median": None, "mean": None, "p90": None, "min": None, "max": None}
    return {
        "n": len(vals),
        "median": round(median(vals), 3),
        "mean": round(mean(vals), 3),
        "p90": round(quantile(vals, 0.9), 3),
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
    }
