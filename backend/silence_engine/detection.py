"""Expected-activity estimation and gap detection.

STEP 1  Expected activity: for each stream-day, the median of the same weekday
        over the previous ``lookback_weeks`` weeks, using only days that were
        scheduled open and whose counts were trusted *as known at that time*.
        Strictly causal: day d's baseline never uses data from day d or later.
STEP 2  Gap measurement: absolute shortfall, percentage shortfall (only when
        expected >= 1), robust z-score, and a one-sided lower CUSUM that
        accumulates persistent moderate declines.
STEP 3  Day status: closed / closed_unusual / ok / low / missing / suspect /
        no_baseline. Missing (no count) and suspect (count present but feed
        health doubtful) are kept separate from low (a trusted low count).
STEP 4  Episodes: runs of non-ok open days (closed days bridge, a single ok day
        is tolerated). An episode becomes an alert once it reaches
        ``min_persistence`` non-ok open days; the alert date is that day.

Thresholds are parameters. The evaluation tunes them on the training period
only and reports test-period performance.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

from . import stats
from .data_quality import DISTRUST_FLAGS, DayRecord, StreamData

STATUSES = ("closed", "closed_unusual", "ok", "low", "missing", "suspect", "no_baseline")
NON_OK = ("low", "missing", "suspect")


@dataclass(frozen=True)
class DetectionParams:
    lookback_weeks: int = 8
    min_history: int = 4
    z_threshold: float = 3.0
    min_shortfall: float = 0.25
    cusum_k: float = 0.5
    cusum_h: float = 4.0
    min_persistence: int = 2
    gap_tolerance: int = 1
    use_feed_health: bool = True  # ablation switch: False = treat every count as trusted, missing as 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "DetectionParams":
        if not data:
            return cls()
        allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**allowed)


@dataclass
class Baseline:
    expected: Optional[float]
    sigma: Optional[float]
    n_history: int
    normally_open: bool
    typical_if_open: Optional[float]


@dataclass
class DayAssessment:
    date: date
    observed: Optional[int]
    value_status: str
    scheduled_open: bool
    schedule_inferred: bool
    normally_open: bool
    expected: Optional[float]
    typical_if_open: Optional[float]
    sigma: Optional[float]
    n_history: int
    z: Optional[float]
    shortfall: Optional[float]
    shortfall_frac: Optional[float]
    trusted: bool
    flags: List[str]
    status: str
    cusum: float = 0.0
    low_reason: Optional[str] = None
    cusum_run_start: Optional[date] = None
    weather: Optional[int] = None
    transport: Optional[bool] = None
    outage: Optional[bool] = None
    batch_completeness: Optional[float] = None
    heartbeat: Optional[bool] = None
    stale_days: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.scheduled_open


@dataclass
class Episode:
    stream_id: str
    kind: str                 # "silence" | "closure"
    start_index: int
    end_index: int
    start: date
    end: date
    onset: date               # estimated start of decline (may precede first non-ok day via CUSUM)
    alert_date: Optional[date]
    nonok_days: int
    ongoing: bool
    day_indices: List[int] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Baselines (independent of thresholds, so they are computed once per dataset)
# ----------------------------------------------------------------------------

def compute_baselines(days: Sequence[DayRecord], lookback_weeks: int = 8, min_history: int = 4,
                      use_feed_health: bool = True) -> List[Baseline]:
    """Causal same-weekday baselines for each day in a stream."""
    index_by_date = {rec.date: i for i, rec in enumerate(days)}
    ratios: List[float] = []  # recent observed/expected ratios for a pooled dispersion estimate
    out: List[Baseline] = []
    for i, rec in enumerate(days):
        history: List[float] = []
        open_flags: List[bool] = []
        for w in range(1, lookback_weeks + 1):
            j = index_by_date.get(rec.date - timedelta(days=7 * w))
            if j is None:
                continue
            past = days[j]
            if past.value_status == "missing_record" and past.schedule_inferred:
                continue  # unknown schedule; ignore for the normal-schedule vote
            open_flags.append(past.scheduled_open)
            if not past.scheduled_open:
                continue
            if use_feed_health:
                if past.observed is None or not past.trusted_as_of(rec.date):
                    continue
                history.append(float(past.observed))
            else:
                history.append(float(past.observed or 0))
        normally_open = (sum(open_flags) / len(open_flags) >= 0.5) if open_flags else rec.scheduled_open
        typical = stats.median(history) if len(history) >= min_history else None
        expected: Optional[float]
        sigma: Optional[float]
        if not rec.scheduled_open:
            expected, sigma = 0.0, None
        elif typical is None:
            expected, sigma = None, None
        else:
            expected = typical
            sigma_mad = stats.robust_sigma(history)
            sigma_pois = math.sqrt(max(expected, 1.0))
            sigma_cv = 0.0
            if len(ratios) >= 10:
                recent = ratios[-42:]
                sigma_cv = stats.MAD_TO_SIGMA * stats.mad(recent) * expected
            sigma = max(sigma_mad, sigma_pois, sigma_cv, 1.0)
        out.append(Baseline(expected, sigma, len(history), normally_open, typical))
        # Update pooled ratio list with today's trusted value (used from tomorrow on).
        if rec.scheduled_open and typical and typical >= 1 and rec.observed is not None:
            if (not use_feed_health) or rec.trusted:
                r = rec.observed / typical
                if 0.5 <= r <= 2.0:  # exclude gross outliers without needing thresholds
                    ratios.append(r)
    return out


# ----------------------------------------------------------------------------
# Day classification (cheap; depends on thresholds)
# ----------------------------------------------------------------------------

def assess_stream(sd: StreamData, baselines: Sequence[Baseline], params: DetectionParams) -> List[DayAssessment]:
    out: List[DayAssessment] = []
    s = 0.0
    run_start: Optional[date] = None
    for rec, base in zip(sd.days, baselines):
        observed = rec.observed
        trusted = rec.trusted
        flags = list(rec.flags)
        if not params.use_feed_health:
            # Ablation: no feed-health checks. Missing counts are read as zero.
            if rec.scheduled_open and observed is None:
                observed = 0
            trusted = rec.scheduled_open and observed is not None
        expected = base.expected
        z = shortfall = shortfall_frac = None
        if rec.scheduled_open and expected is not None and observed is not None:
            shortfall = expected - observed
            shortfall_frac = (shortfall / expected) if expected >= 1 else None
            z = (observed - expected) / base.sigma if base.sigma else None

        if not rec.scheduled_open:
            status = "closed_unusual" if base.normally_open else "closed"
        elif observed is None:
            status = "missing"
        elif params.use_feed_health and not trusted:
            status = "suspect"
        elif expected is None:
            status = "no_baseline"
        else:
            status = "ok"

        low_reason = None
        if status == "ok" and z is not None:
            # Cap the statistic so one extreme day cannot keep the alarm on long after recovery.
            s_new = min(params.cusum_h * 1.5, max(0.0, s - z - params.cusum_k))
            if s == 0.0 and s_new > 0.0:
                run_start = rec.date
            s = s_new
            if s == 0.0:
                run_start = None
            frac = shortfall_frac if shortfall_frac is not None else 0.0
            if z <= -params.z_threshold and frac >= params.min_shortfall:
                status, low_reason = "low", "deviation"
            elif s >= params.cusum_h and frac >= params.min_shortfall / 2:
                status, low_reason = "low", "cusum"
        out.append(DayAssessment(
            date=rec.date, observed=observed, value_status=rec.value_status,
            scheduled_open=rec.scheduled_open, schedule_inferred=rec.schedule_inferred,
            normally_open=base.normally_open, expected=expected, typical_if_open=base.typical_if_open,
            sigma=base.sigma, n_history=base.n_history, z=z, shortfall=shortfall,
            shortfall_frac=shortfall_frac, trusted=trusted, flags=flags, status=status, cusum=round(s, 3),
            low_reason=low_reason, cusum_run_start=run_start if low_reason == "cusum" else None,
            weather=rec.weather, transport=rec.transport, outage=rec.outage,
            batch_completeness=rec.batch_completeness, heartbeat=rec.heartbeat, stale_days=rec.stale_days,
        ))
    return out


# ----------------------------------------------------------------------------
# Episodes
# ----------------------------------------------------------------------------

def find_episodes(stream_id: str, days: Sequence[DayAssessment], params: DetectionParams,
                  as_of: Optional[date] = None) -> List[Episode]:
    episodes: List[Episode] = []
    if not days:
        return episodes
    last_date = as_of or days[-1].date

    # Silence episodes: runs of non-ok open days.
    current: List[int] = []
    tolerance_used = 0
    for i, a in enumerate(days):
        if not a.scheduled_open:
            continue  # bridge closed days
        if a.status in NON_OK:
            current.append(i)
            tolerance_used = 0
        elif current:
            tolerance_used += 1
            if tolerance_used > params.gap_tolerance:
                _close_silence(stream_id, days, current, params, last_date, episodes)
                current, tolerance_used = [], 0
    if current:
        _close_silence(stream_id, days, current, params, last_date, episodes)

    # Closure episodes: normally-open days that the schedule lists as closed.
    run: List[int] = []
    for i, a in enumerate(days):
        if a.status == "closed_unusual":
            run.append(i)
        elif a.scheduled_open:
            if run:
                episodes.append(_closure(stream_id, days, run, last_date))
            run = []
    if run:
        episodes.append(_closure(stream_id, days, run, last_date))
    episodes.sort(key=lambda e: e.start)
    return episodes


def _close_silence(stream_id: str, days: Sequence[DayAssessment], idx: List[int], params: DetectionParams,
                   last_date: date, out: List[Episode]) -> None:
    if len(idx) < params.min_persistence:
        return
    alert_day = days[idx[params.min_persistence - 1]].date
    first = days[idx[0]]
    onset = first.date
    for i in idx:
        if days[i].low_reason == "cusum" and days[i].cusum_run_start:
            onset = min(onset, days[i].cusum_run_start)
            break
    end = days[idx[-1]].date
    start_index = idx[0]
    while start_index > 0 and days[start_index].date > onset:
        start_index -= 1
    out.append(Episode(
        stream_id=stream_id, kind="silence", start_index=start_index, end_index=idx[-1],
        start=days[start_index].date, end=end, onset=onset, alert_date=alert_day,
        nonok_days=len(idx), ongoing=(last_date - end).days <= 1, day_indices=list(idx),
    ))


def _closure(stream_id: str, days: Sequence[DayAssessment], idx: List[int], last_date: date) -> Episode:
    return Episode(
        stream_id=stream_id, kind="closure", start_index=idx[0], end_index=idx[-1],
        start=days[idx[0]].date, end=days[idx[-1]].date, onset=days[idx[0]].date,
        alert_date=None, nonok_days=0, ongoing=(last_date - days[idx[-1]].date).days <= 1,
        day_indices=list(idx),
    )


def has_feed_distrust(a: DayAssessment) -> bool:
    return any(f in a.flags for f in DISTRUST_FLAGS)
