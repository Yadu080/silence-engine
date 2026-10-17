"""Feed-health and data-quality checks.

Turns raw validated ``Observation`` rows into one ``DayRecord`` per stream per
calendar day, and independently flags reporting problems:

  missing records, missing values, malformed values, heartbeat gaps, stale
  feeds, incomplete upload batches, zero-filled placeholders, flatlines,
  duplicate rows (and conflicting duplicates), activity on scheduled-closed
  days and late arrivals.

A day whose record is absent stays ``observed=None``; it is never converted to
zero. A day is ``trusted`` only when a count exists, the service was scheduled
open, and no feed-health flag casts doubt on the count.

All checks are causal: a flag for day *d* uses only data for days <= *d*, and
``distrust_known_on`` records when a retroactive flag (flatline) became known,
so the detector can avoid using information from the future.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import stats
from .ingest import Observation

# Flags that make a reported count untrustworthy as evidence of activity level.
DISTRUST_FLAGS = ("heartbeat_missing", "stale_feed", "incomplete_batches", "zero_fill_suspected", "flatline")
MISSING_FLAGS = ("missing_record", "missing_value", "malformed")
STALE_AFTER_DAYS = 1.0
INCOMPLETE_BATCH_RATIO = 0.75
LATE_AFTER_HOURS = 6.0  # received more than 6 h after the end of the service day

ISSUE_LABELS = {
    "missing_record": "No record received",
    "missing_value": "Record without a count",
    "malformed": "Malformed count value",
    "heartbeat_missing": "Feed heartbeat missing",
    "stale_feed": "Stale feed (source not seen)",
    "incomplete_batches": "Incomplete upload batches",
    "zero_fill_suspected": "Zero reported while feed was down",
    "flatline": "Flatlined (repeated) values",
    "duplicate": "Duplicate records",
    "duplicate_conflict": "Conflicting duplicate records",
    "activity_on_closed_day": "Activity on a scheduled-closed day",
    "late_arrival": "Record arrived late",
}
ISSUE_SEVERITY = {
    "missing_record": "high", "missing_value": "high", "malformed": "medium",
    "heartbeat_missing": "medium", "stale_feed": "high", "incomplete_batches": "medium",
    "zero_fill_suspected": "high", "flatline": "high", "duplicate": "low",
    "duplicate_conflict": "medium", "activity_on_closed_day": "medium", "late_arrival": "low",
}


@dataclass
class DayRecord:
    date: date
    observed: Optional[int]
    value_status: str                     # ok | missing_record | missing_value | malformed
    scheduled_open: bool
    schedule_inferred: bool = False
    heartbeat: Optional[bool] = None
    stale_days: Optional[float] = None
    batch_completeness: Optional[float] = None
    batches_expected: Optional[int] = None
    batches_received: Optional[int] = None
    feed_last_seen: Optional[datetime] = None
    received_at: Optional[datetime] = None
    delay_days: Optional[float] = None
    duplicate_rows: int = 0
    weather: Optional[int] = None
    transport: Optional[bool] = None
    outage: Optional[bool] = None
    flags: List[str] = field(default_factory=list)
    trusted: bool = False
    distrust_known_on: Optional[date] = None  # when the distrust became knowable

    def trusted_as_of(self, d: date) -> bool:
        """Was this day's count considered trustworthy using information available before day d?"""
        if self.trusted:
            return True
        if self.observed is None or not self.scheduled_open:
            return False
        # Distrust learned only after day d could not have been used when assessing d.
        return self.distrust_known_on is not None and self.distrust_known_on > d


@dataclass
class StreamData:
    stream_id: str
    site_id: str
    service_type: str
    days: List[DayRecord]

    @property
    def first_date(self) -> date:
        return self.days[0].date

    @property
    def last_date(self) -> date:
        return self.days[-1].date


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=timezone.utc)


def _received(o: Observation) -> datetime:
    return o.report_received_at or _end_of_day(o.service_date)


def _pick_canonical(rows: List[Observation]) -> Tuple[Observation, int, bool]:
    """Resolve duplicates: keep the most recently received row."""
    if len(rows) == 1:
        return rows[0], 0, False
    ordered = sorted(rows, key=_received)
    counts = {(r.observed_count, r.value_status) for r in rows}
    return ordered[-1], len(rows) - 1, len(counts) > 1


def prepare_streams(observations: Iterable[Observation], end_date: Optional[date] = None,
                    visible_at: Optional[datetime] = None) -> Dict[str, StreamData]:
    """Build per-stream daily records.

    end_date   last calendar day to assess (default: latest service_date seen)
    visible_at only rows received at or before this instant are used, which
               simulates what an operational system would know at that time.
    """
    grouped: Dict[str, Dict[date, List[Observation]]] = defaultdict(lambda: defaultdict(list))
    meta: Dict[str, Tuple[str, str]] = {}
    latest: Optional[date] = None
    for o in observations:
        if visible_at is not None and _received(o) > visible_at:
            continue
        grouped[o.stream_id][o.service_date].append(o)
        meta[o.stream_id] = (o.site_id, o.service_type)
        if latest is None or o.service_date > latest:
            latest = o.service_date
    if end_date is None:
        end_date = latest
    streams: Dict[str, StreamData] = {}
    if end_date is None:
        return streams
    for stream_id in sorted(grouped):
        by_date = grouped[stream_id]
        first = min(by_date)
        if first > end_date:
            continue
        site_id, service_type = meta[stream_id]
        days = _build_days(by_date, first, end_date)
        _flag_flatlines(days)
        _flag_closed_day_activity(days)
        for rec in days:
            _finalise_trust(rec)
        streams[stream_id] = StreamData(stream_id, site_id, service_type, days)
    return streams


def _build_days(by_date: Dict[date, List[Observation]], first: date, end: date) -> List[DayRecord]:
    days: List[DayRecord] = []
    weekday_history: Dict[int, List[bool]] = defaultdict(list)
    d = first
    while d <= end:
        rows = by_date.get(d)
        if not rows:
            history = weekday_history[d.weekday()][-8:]
            inferred_open = (sum(history) / len(history) >= 0.5) if history else True
            rec = DayRecord(date=d, observed=None, value_status="missing_record",
                            scheduled_open=inferred_open, schedule_inferred=True)
            if inferred_open:
                rec.flags.append("missing_record")
            days.append(rec)
            d += timedelta(days=1)
            continue
        row, dup_rows, conflict = _pick_canonical(rows)
        status = {"ok": "ok", "missing": "missing_value", "malformed": "malformed"}[row.value_status]
        rec = DayRecord(
            date=d,
            observed=row.observed_count if status == "ok" else None,
            value_status=status,
            scheduled_open=row.scheduled_open,
            heartbeat=row.feed_heartbeat_received,
            batches_expected=row.batches_expected,
            batches_received=row.batches_received,
            feed_last_seen=row.feed_last_seen,
            received_at=row.report_received_at,
            duplicate_rows=dup_rows,
            weather=row.weather_severity,
            transport=row.transport_disruption,
            outage=row.reported_outage,
        )
        weekday_history[d.weekday()].append(row.scheduled_open)
        if dup_rows:
            rec.flags.append("duplicate_conflict" if conflict else "duplicate")
        if status != "ok" and rec.scheduled_open:
            rec.flags.append(status)
        elif status == "malformed":
            rec.flags.append("malformed")
        eod = _end_of_day(d)
        if row.feed_last_seen is not None:
            rec.stale_days = round((eod - row.feed_last_seen).total_seconds() / 86400.0, 3)
            if rec.scheduled_open and rec.stale_days > STALE_AFTER_DAYS:
                rec.flags.append("stale_feed")
        if row.report_received_at is not None:
            rec.delay_days = round((row.report_received_at - eod).total_seconds() / 86400.0, 3)
            if rec.delay_days * 24.0 > LATE_AFTER_HOURS:
                rec.flags.append("late_arrival")
        if rec.scheduled_open:
            if row.feed_heartbeat_received is False:
                rec.flags.append("heartbeat_missing")
            if row.batches_expected:
                received = row.batches_received if row.batches_received is not None else 0
                rec.batch_completeness = round(received / row.batches_expected, 3)
                if rec.batch_completeness < INCOMPLETE_BATCH_RATIO:
                    rec.flags.append("incomplete_batches")
            if rec.observed == 0 and (row.feed_heartbeat_received is False or
                                      (row.batches_expected and not row.batches_received)):
                rec.flags.append("zero_fill_suspected")
        days.append(rec)
        d += timedelta(days=1)
    return days


def _trailing_level(days: Sequence[DayRecord], end_index: int, window: int = 14) -> Optional[float]:
    values: List[int] = []
    i = end_index
    while i >= 0 and len(values) < window:
        rec = days[i]
        if rec.scheduled_open and rec.observed is not None and not any(f in rec.flags for f in DISTRUST_FLAGS):
            values.append(rec.observed)
        i -= 1
    return stats.median(values) if len(values) >= 5 else None


def _flag_flatlines(days: List[DayRecord]) -> None:
    """Flag runs of identical non-zero counts on consecutive open days.

    The run length that triggers a flag adapts to the stream's level: a run is
    flagged when ordinary Poisson-like noise would produce it with probability
    below 1e-4. Zeros are excluded (a run of zeros may be a real closure and is
    handled by the zero-fill check, which needs feed evidence).
    """
    run: List[int] = []
    run_value: Optional[int] = None
    level_at_start: Optional[float] = None
    flagged_run = False
    for i, rec in enumerate(days):
        if not rec.scheduled_open:
            continue  # closed days neither extend nor break a run
        value = rec.observed
        if value is not None and value > 0 and value == run_value:
            run.append(i)
        else:
            run = [i] if (value is not None and value > 0) else []
            run_value = value if (value is not None and value > 0) else None
            level_at_start = _trailing_level(days, i - 1) if run else None
            flagged_run = False
        if not run or level_at_start is None:
            continue
        needed = stats.flatline_run_length(level_at_start)
        if len(run) >= needed:
            known_on = rec.date
            targets = run if not flagged_run else [i]
            for j in targets:
                if "flatline" not in days[j].flags:
                    days[j].flags.append("flatline")
                    days[j].distrust_known_on = known_on if days[j].distrust_known_on is None else days[j].distrust_known_on
            flagged_run = True


def _flag_closed_day_activity(days: List[DayRecord]) -> None:
    for i, rec in enumerate(days):
        if rec.scheduled_open or rec.observed is None:
            continue
        level = _trailing_level(days, i - 1, window=20)
        if level is not None and rec.observed > max(5.0, 0.25 * level):
            rec.flags.append("activity_on_closed_day")


def _finalise_trust(rec: DayRecord) -> None:
    distrust = [f for f in rec.flags if f in DISTRUST_FLAGS]
    rec.trusted = rec.scheduled_open and rec.observed is not None and not distrust
    if any(f != "flatline" for f in distrust) or (distrust and rec.distrust_known_on is None):
        rec.distrust_known_on = rec.date  # same-day flags are known immediately


def is_feed_problem(rec: DayRecord) -> bool:
    """Open day whose count is missing or untrustworthy."""
    if not rec.scheduled_open:
        return False
    return rec.observed is None or any(f in rec.flags for f in DISTRUST_FLAGS)


# ----------------------------------------------------------------------------
# Reporting for the Data Quality view
# ----------------------------------------------------------------------------

def collect_issues(streams: Dict[str, StreamData]) -> List[dict]:
    """Group per-day flags into runs per stream and issue type."""
    issues: List[dict] = []
    for stream_id, sd in streams.items():
        open_runs: Dict[str, dict] = {}
        for rec in sd.days:
            present = set(rec.flags)
            for kind in list(open_runs):
                if kind not in present and (rec.scheduled_open or kind == "activity_on_closed_day"):
                    issues.append(open_runs.pop(kind))
            for kind in present:
                run = open_runs.get(kind)
                if run is None:
                    open_runs[kind] = {
                        "stream_id": stream_id, "site_id": sd.site_id, "service_type": sd.service_type,
                        "type": kind, "label": ISSUE_LABELS.get(kind, kind), "severity": ISSUE_SEVERITY.get(kind, "low"),
                        "start": rec.date.isoformat(), "end": rec.date.isoformat(), "days": 1,
                    }
                else:
                    run["end"] = rec.date.isoformat()
                    run["days"] += 1
        issues.extend(open_runs.values())
    issues.sort(key=lambda x: (x["end"], x["start"]), reverse=True)
    for n, issue in enumerate(issues, start=1):
        issue["issue_id"] = f"DQ-{n:05d}"
    return issues


def stream_health(sd: StreamData, as_of: date, recent_days: int = 28) -> dict:
    """Feed-health summary for one stream."""
    open_days = [r for r in sd.days if r.scheduled_open]
    recent = [r for r in open_days if (as_of - r.date).days < recent_days]
    last_good = next((r.date for r in reversed(sd.days) if r.trusted), None)
    last_record = next((r.date for r in reversed(sd.days) if r.value_status != "missing_record"), None)
    recent_problem = [r for r in recent if is_feed_problem(r)]
    tail = [r for r in open_days if (as_of - r.date).days < 14]
    last_two = open_days[-2:]
    if len(last_two) >= 2 and all(is_feed_problem(r) for r in last_two):
        status = "FAILED"
    elif sum(1 for r in tail if is_feed_problem(r)) >= 2:
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    def count(flag: str, rows: Sequence[DayRecord]) -> int:
        return sum(1 for r in rows if flag in r.flags)

    def rate(n: int, d: int) -> Optional[float]:
        return round(n / d, 4) if d else None

    return {
        "stream_id": sd.stream_id,
        "site_id": sd.site_id,
        "service_type": sd.service_type,
        "status": status,
        "first_date": sd.first_date.isoformat(),
        "last_record_date": last_record.isoformat() if last_record else None,
        "last_trusted_date": last_good.isoformat() if last_good else None,
        "days_since_trusted": (as_of - last_good).days if last_good else None,
        "open_days_total": len(open_days),
        "open_days_recent": len(recent),
        "completeness_total": rate(sum(1 for r in open_days if r.observed is not None), len(open_days)),
        "completeness_recent": rate(sum(1 for r in recent if r.observed is not None), len(recent)),
        "trusted_share_recent": rate(sum(1 for r in recent if r.trusted), len(recent)),
        "problem_days_recent": len(recent_problem),
        "counts_total": {k: count(k, sd.days) for k in ISSUE_LABELS},
        "counts_recent": {k: count(k, [r for r in sd.days if (as_of - r.date).days < recent_days]) for k in ISSUE_LABELS},
        "duplicate_rows_total": sum(r.duplicate_rows for r in sd.days),
        "late_share": rate(count("late_arrival", sd.days), sum(1 for r in sd.days if r.received_at)),
    }
