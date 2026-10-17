"""Reproducible SYNTHETIC service-activity generator.

Everything produced here is fictional: the locations, the counts and the
events. The generator returns two strictly separate products:

  1. ``raw_records`` — what a monitoring system would actually receive
     (aggregate daily counts plus feed metadata and context signals). These go
     through the same validation path as CSV imports.
  2. ``labels`` — ground-truth event windows, used ONLY by the evaluation
     module. They are written to a separate JSON file and never stored in the
     observations table.

Determinism: each stream uses its own ``random.Random`` seeded from the global
seed and the stream's index, so the same seed yields the same dataset on any
platform (the SHA-256 fingerprint in the manifest lets you verify this on
macOS and LinuxONE).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 42
DEFAULT_START = date(2025, 10, 6)  # a Monday
DEFAULT_DAYS = 364
WARMUP_DAYS = 56  # history needed before events are injected

EVENT_CLASSES = ("ACCESS_GAP", "REPORTING_FAILURE", "AMBIGUOUS", "SCHEDULED_CLOSURE", "NORMAL_VARIATION")

# Fictional public holidays: non-essential services close.
HOLIDAYS = (date(2025, 12, 25), date(2026, 1, 1), date(2026, 5, 25), date(2026, 8, 31))


@dataclass(frozen=True)
class StreamSpec:
    service_type: str
    base: float                     # typical daily volume on a factor-1.0 weekday
    dow: Tuple[float, ...]          # Mon..Sun demand factor; 0 = normally closed
    seasonal_amp: float = 0.08      # amplitude of the annual cycle
    peak_doy: int = 15              # day-of-year of seasonal peak
    trend: float = 0.0              # relative drift over the whole period
    dispersion_k: float = 50.0      # gamma-Poisson shape; lower = noisier
    batches: int = 8                # upload batches per full open day
    essential: bool = False         # stays open on public holidays


@dataclass(frozen=True)
class SiteSpec:
    site_id: str
    name: str
    region: str
    streams: Tuple[StreamSpec, ...]
    opens_on_day: int = 0           # day index when the site starts operating


WEEKDAYS = (1, 1, 1, 1, 1, 0, 0)

SITES: Tuple[SiteSpec, ...] = (
    SiteSpec("NGT", "Northgate Community Clinic", "North", (
        StreamSpec("primary_care", 220, (1.0, 0.95, 0.92, 0.95, 0.9, 0.45, 0.0), 0.12, 20, -0.03, 60, 10),
        StreamSpec("vaccination", 48, (1.0, 1.0, 1.0, 1.0, 0.95, 0.0, 0.0), 0.35, 320, 0.0, 35, 8),
    )),
    SiteSpec("RVS", "Riverside Benefits Office", "Central", (
        StreamSpec("benefits_applications", 85, (1.15, 1.0, 0.95, 0.95, 0.9, 0.0, 0.0), 0.06, 40, 0.04, 50, 8),
        StreamSpec("benefits_inquiries", 140, (1.2, 1.05, 1.0, 0.95, 0.85, 0.0, 0.0), 0.05, 40, 0.02, 55, 8),
    )),
    SiteSpec("EFD", "Eastfield Food Distribution Hub", "East", (
        StreamSpec("food_distribution", 125, (0.9, 1.0, 1.0, 1.05, 1.15, 1.2, 0.0), 0.08, 355, 0.05, 45, 6),
    )),
    SiteSpec("HBR", "Harbor Street Pharmacy Outreach", "Central", (
        StreamSpec("pharmacy_dispensing", 175, (1.1, 1.0, 1.0, 1.0, 1.05, 0.8, 0.55), 0.07, 30, 0.0, 70, 12, True),
    )),
    SiteSpec("MLB", "Millbrook Rural Health Post", "Rural", (
        StreamSpec("primary_care", 15, WEEKDAYS, 0.10, 25, 0.0, 25, 4),
    )),
    SiteSpec("CLB", "Central Library Digital Services", "Central", (
        StreamSpec("digital_assistance", 38, (1.0, 1.0, 1.0, 1.0, 0.9, 0.7, 0.0), 0.05, 260, 0.06, 30, 6),
    )),
    SiteSpec("WSW", "Westside Water Point", "West", (
        StreamSpec("water_distribution", 410, (1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.05), 0.15, 200, 0.0, 80, 24, True),
    )),
    SiteSpec("HLC", "Hillcrest Mobile Clinic", "Rural", (
        StreamSpec("mobile_clinic", 7, (0.0, 1.0, 0.0, 1.0, 0.0, 1.2, 0.0), 0.0, 1, 0.0, 20, 2),
    )),
    SiteSpec("SBK", "Southbank Pop-up Support Hub", "East", (
        StreamSpec("community_support", 55, WEEKDAYS, 0.04, 100, 0.0, 40, 6),
    ), opens_on_day=210),
)

MALFORMED_TOKENS = ("-3", "12O", "#REF!", "999999999", "4.5", "??")


@dataclass
class Event:
    event_id: str
    stream_id: str
    site_id: str
    service_type: str
    start: date
    end: date
    event_class: str
    subtype: str
    magnitude: Optional[float] = None
    params: Dict[str, Any] = field(default_factory=dict)
    showcase: bool = False

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end

    def to_label(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stream_id": self.stream_id,
            "site_id": self.site_id,
            "service_type": self.service_type,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "class": self.event_class,
            "subtype": self.subtype,
            "magnitude": None if self.magnitude is None else round(self.magnitude, 3),
            "showcase": self.showcase,
        }


@dataclass
class GeneratedDataset:
    raw_records: List[Dict[str, Any]]
    sites: Dict[str, Dict[str, Any]]
    labels: Dict[str, Any]
    manifest: Dict[str, Any]


# ----------------------------------------------------------------------------
# Random helpers (stdlib only; deterministic for a given Random instance)
# ----------------------------------------------------------------------------

def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam < 30:
        limit = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def _negbin(rng: random.Random, lam: float, k: float) -> int:
    if lam <= 0:
        return 0
    return _poisson(rng, lam * rng.gammavariate(k, 1.0 / k))


def _stream_seed(seed: int, index: int, salt: int) -> int:
    return (seed * 1_000_003 + index * 7_919 + salt * 104_729) % (2**31 - 1)


# ----------------------------------------------------------------------------
# Context (weather / transport / outage) at site level
# ----------------------------------------------------------------------------

def _weather_start_prob(d: date) -> float:
    if d.month in (12, 1, 2):
        return 0.06
    if d.month in (11, 3):
        return 0.03
    if d.month in (7, 8):
        return 0.025
    return 0.012


def _background_context(seed: int, dates: Sequence[date]) -> Dict[Tuple[str, date], Dict[str, Any]]:
    """Weather by region, transport/outage by site. Small demand effects only."""
    ctx: Dict[Tuple[str, date], Dict[str, Any]] = {}
    regions = sorted({s.region for s in SITES})
    region_weather: Dict[Tuple[str, date], int] = {}
    for r_index, region in enumerate(regions):
        rng = random.Random(_stream_seed(seed, r_index, 11))
        i = 0
        while i < len(dates):
            if rng.random() < _weather_start_prob(dates[i]):
                length = rng.randint(1, 3)
                severity = rng.choices((1, 2, 3), weights=(50, 35, 15))[0]
                for j in range(i, min(len(dates), i + length)):
                    region_weather[(region, dates[j])] = severity
                i += length
            else:
                i += 1
    for s_index, site in enumerate(SITES):
        rng = random.Random(_stream_seed(seed, s_index, 13))
        transport_left = 0
        for d in dates:
            entry = {"weather": region_weather.get((site.region, d), 0), "transport": False, "outage": False}
            if transport_left > 0:
                entry["transport"] = True
                transport_left -= 1
            elif rng.random() < 0.012:
                entry["transport"] = True
                transport_left = rng.randint(0, 1)
            if rng.random() < 0.004:
                entry["outage"] = True
            ctx[(site.site_id, d)] = entry
    return ctx


def _background_effect(entry: Dict[str, Any]) -> float:
    effect = {0: 1.0, 1: 1.0, 2: 0.96, 3: 0.9}[entry["weather"]]
    if entry["transport"]:
        effect *= 0.97
    if entry["outage"]:
        effect *= 0.95
    return effect


# ----------------------------------------------------------------------------
# Event scheduling
# ----------------------------------------------------------------------------

def _is_open_day(spec: StreamSpec, d: date) -> bool:
    return spec.dow[d.weekday()] > 0


def _closed_for_holiday(spec: StreamSpec, d: date) -> bool:
    return (d in HOLIDAYS) and not spec.essential and _is_open_day(spec, d)


def _open_days_between(spec: StreamSpec, start: date, end: date) -> int:
    n, d = 0, start
    while d <= end:
        if _is_open_day(spec, d) and not _closed_for_holiday(spec, d):
            n += 1
        d += timedelta(days=1)
    return n


DURATIONS = {
    "ACCESS_GAP": (3, 12),
    "REPORTING_FAILURE": (2, 9),
    "AMBIGUOUS": (3, 8),
    "SCHEDULED_CLOSURE": (1, 4),
    "NORMAL_VARIATION": (2, 6),
}
CLASS_WEIGHTS = {
    "ACCESS_GAP": 30,
    "REPORTING_FAILURE": 30,
    "AMBIGUOUS": 13,
    "SCHEDULED_CLOSURE": 10,
    "NORMAL_VARIATION": 17,
}

# Deliberate events in the final weeks so the demo has current examples.
# They are labelled and evaluated exactly like every other event.
SHOWCASE = (
    ("EFD:food_distribution", -8, 0, "ACCESS_GAP", "transport", 0.62),
    ("RVS:benefits_applications", -5, 0, "REPORTING_FAILURE", "zero_fill", None),
    ("NGT:primary_care", -20, -14, "AMBIGUOUS", "mixed_signals", 0.4),
    ("WSW:water_distribution", -16, -11, "REPORTING_FAILURE", "stale_flatline", None),
    ("MLB:primary_care", -12, -6, "ACCESS_GAP", "weather", 0.72),
    ("CLB:digital_assistance", -3, -2, "SCHEDULED_CLOSURE", "staff_training", None),
)


def _subtype(rng: random.Random, event_class: str) -> str:
    if event_class == "ACCESS_GAP":
        return rng.choices(("weather", "transport", "service_outage", "unexplained"), weights=(35, 30, 15, 20))[0]
    if event_class == "REPORTING_FAILURE":
        return rng.choices(("dropout", "zero_fill", "stale_flatline", "partial_batches", "covert"),
                           weights=(30, 25, 25, 12, 8))[0]
    if event_class == "AMBIGUOUS":
        return "mixed_signals"
    if event_class == "SCHEDULED_CLOSURE":
        return rng.choice(("staff_training", "maintenance", "planned_relocation"))
    return "benign_demand_lull"


def _magnitude(rng: random.Random, event_class: str, subtype: str) -> Optional[float]:
    if event_class == "ACCESS_GAP":
        if subtype == "service_outage":
            return 1.0 if rng.random() < 0.3 else rng.uniform(0.5, 0.9)
        return rng.uniform(0.35, 0.8)
    if event_class == "AMBIGUOUS":
        return rng.uniform(0.25, 0.55)
    if event_class == "NORMAL_VARIATION":
        return rng.uniform(0.08, 0.22)
    if event_class == "REPORTING_FAILURE" and subtype == "partial_batches":
        return rng.uniform(0.4, 0.8)  # share of batches lost
    if event_class == "REPORTING_FAILURE" and subtype == "covert":
        return rng.uniform(0.4, 0.6)
    return None


def _schedule_stream_events(rng: random.Random, spec: StreamSpec, site: SiteSpec, stream_id: str,
                            dates: Sequence[date], counter: List[int]) -> List[Event]:
    first = max(WARMUP_DAYS, site.opens_on_day + WARMUP_DAYS)
    last_index = len(dates) - 1
    events: List[Event] = []
    showcase = [s for s in SHOWCASE if s[0] == stream_id]
    random_until = last_index - 2
    if showcase:
        random_until = min(last_index + s[1] for s in showcase) - 10

    def new_id() -> str:
        counter[0] += 1
        return f"EV-{counter[0]:04d}"

    i = first + rng.randint(0, 9)
    while i < random_until - 2:
        event_class = rng.choices(list(CLASS_WEIGHTS), weights=list(CLASS_WEIGHTS.values()))[0]
        lo, hi = DURATIONS[event_class]
        duration = rng.randint(lo, hi)
        start = dates[i]
        end_index = min(i + duration - 1, random_until)
        # Require enough open days for the event to be observable at all.
        while _open_days_between(spec, start, dates[end_index]) < (1 if event_class == "SCHEDULED_CLOSURE" else 2) \
                and end_index < random_until:
            end_index += 1
        end = dates[end_index]
        overlaps_holiday = any(start <= h <= end for h in HOLIDAYS)
        if _open_days_between(spec, start, end) >= 1 and not overlaps_holiday:
            subtype = _subtype(rng, event_class)
            events.append(Event(new_id(), stream_id, site.site_id, spec.service_type, start, end,
                                event_class, subtype, _magnitude(rng, event_class, subtype)))
        i = end_index + 1 + rng.randint(9, 22)

    for _, start_off, end_off, event_class, subtype, magnitude in showcase:
        events.append(Event(new_id(), stream_id, site.site_id, spec.service_type,
                            dates[last_index + start_off], dates[last_index + end_off],
                            event_class, subtype, magnitude, showcase=True))

    # Public holidays are scheduled closures for non-essential services.
    for h in HOLIDAYS:
        if dates[0] + timedelta(days=first) <= h <= dates[-1] and _closed_for_holiday(spec, h):
            events.append(Event(new_id(), stream_id, site.site_id, spec.service_type, h, h,
                                "SCHEDULED_CLOSURE", "public_holiday"))
    events.sort(key=lambda e: e.start)
    return events


# ----------------------------------------------------------------------------
# Main generator
# ----------------------------------------------------------------------------

def _ts(d: date, hour: int, minute: int = 0, delay_days: int = 0) -> str:
    dt = datetime.combine(d + timedelta(days=delay_days), time(hour, minute), tzinfo=timezone.utc)
    return dt.isoformat()


def generate(seed: int = DEFAULT_SEED, start: date = DEFAULT_START, days: int = DEFAULT_DAYS) -> GeneratedDataset:
    if days < WARMUP_DAYS + 60:
        raise ValueError(f"days must be at least {WARMUP_DAYS + 60} to allow baselines and evaluation")
    dates = [start + timedelta(days=i) for i in range(days)]
    context = _background_context(seed, dates)
    raw_records: List[Dict[str, Any]] = []
    events: List[Event] = []
    dq_injections: List[Dict[str, Any]] = []
    counter = [0]
    stream_index = 0

    # First pass: schedule events for every stream and stamp site-level context.
    scheduled: List[Tuple[SiteSpec, StreamSpec, str, int, List[Event]]] = []
    for site in SITES:
        for spec in site.streams:
            stream_id = f"{site.site_id}:{spec.service_type}"
            rng_events = random.Random(_stream_seed(seed, stream_index, 1))
            stream_events = _schedule_stream_events(rng_events, spec, site, stream_id, dates, counter)
            for ev in stream_events:
                _stamp_event_context(rng_events, ev, site, context)
            scheduled.append((site, spec, stream_id, stream_index, stream_events))
            events.extend(stream_events)
            stream_index += 1

    # Second pass: simulate activity and the reporting layer.
    for site, spec, stream_id, idx, stream_events in scheduled:
        rng = random.Random(_stream_seed(seed, idx, 2))
        last_good_seen: Optional[str] = None
        last_recorded: Optional[int] = None
        mismatch_day = rng.randint(WARMUP_DAYS, days - 1) if rng.random() < 0.6 else None
        for t, d in enumerate(dates):
            if t < site.opens_on_day:
                continue
            ev = next((e for e in stream_events if e.covers(d)), None)
            ctx = context[(site.site_id, d)]
            normally_open = _is_open_day(spec, d)
            scheduled_open = normally_open and not _closed_for_holiday(spec, d)
            if ev and ev.event_class == "SCHEDULED_CLOSURE":
                scheduled_open = False
            actually_open = scheduled_open

            # Latent demand.
            doy = d.timetuple().tm_yday
            seasonal = 1.0 + spec.seasonal_amp * math.cos(2 * math.pi * (doy - spec.peak_doy) / 365.25)
            trend = 1.0 + spec.trend * (t / days)
            lam = spec.base * spec.dow[d.weekday()] * seasonal * trend * _background_effect(ctx) if actually_open else 0.0
            if ev and actually_open:
                lam *= _real_effect(ev, d)
            true_count = _negbin(rng, lam, spec.dispersion_k) if lam > 0 else 0

            # Outdated schedule feed: the schedule says closed but the site operated.
            schedule_flag = scheduled_open
            if mismatch_day == t and scheduled_open and not ev:
                schedule_flag = False
                dq_injections.append({"kind": "schedule_mismatch", "stream_id": stream_id, "date": d.isoformat()})

            half_day = spec.dow[d.weekday()] < 0.6
            batches_expected = (max(1, spec.batches // 2) if half_day else spec.batches) if scheduled_open else 0
            delay = rng.choices((0, 1, 2, 3), weights=(86, 10, 3, 1))[0]
            hour = rng.randint(17, 21)
            record: Dict[str, Any] = {
                "service_date": d.isoformat(),
                "site_id": site.site_id,
                "service_type": spec.service_type,
                "observed_count": true_count,
                "scheduled_open": schedule_flag,
                "feed_heartbeat_received": True,
                "feed_last_seen": _ts(d, hour, rng.randint(0, 59), delay),
                "report_received_at": _ts(d, min(23, hour + 1), rng.randint(0, 59), delay),
                "batches_expected": batches_expected,
                "batches_received": batches_expected,
                "weather_severity": ctx["weather"],
                "transport_disruption": ctx["transport"],
                "reported_outage": ctx["outage"],
            }
            # Ordinary feed noise.
            if scheduled_open and rng.random() < 0.01:
                record["feed_heartbeat_received"] = False
            if batches_expected > 1 and rng.random() < 0.02:
                record["batches_received"] = batches_expected - 1

            emit = True
            if ev and ev.event_class in ("REPORTING_FAILURE", "AMBIGUOUS"):
                emit = _apply_reporting_effect(rng, ev, record, true_count, last_good_seen, last_recorded, d, t)
            elif scheduled_open and rng.random() < 0.004:
                emit = False
                dq_injections.append({"kind": "random_missing", "stream_id": stream_id, "date": d.isoformat()})

            if not emit:
                continue
            if record["feed_heartbeat_received"] and record["feed_last_seen"] and not (
                    ev and ev.event_class == "REPORTING_FAILURE" and ev.subtype in ("zero_fill", "stale_flatline", "dropout")):
                last_good_seen = record["feed_last_seen"]
            if isinstance(record["observed_count"], int) and not (ev and ev.event_class == "REPORTING_FAILURE"):
                last_recorded = record["observed_count"]

            # Malformed values (occasional ETL corruption).
            if scheduled_open and rng.random() < 0.003:
                record["observed_count"] = rng.choice(MALFORMED_TOKENS)
                dq_injections.append({"kind": "malformed", "stream_id": stream_id, "date": d.isoformat()})
            raw_records.append(record)

            # Duplicate ingestion (sometimes with a conflicting value).
            if rng.random() < 0.008:
                dup = dict(record)
                conflicting = isinstance(record["observed_count"], int) and rng.random() < 0.3
                if conflicting:
                    dup["observed_count"] = max(0, record["observed_count"] + rng.choice((-3, -2, 2, 4)))
                dup["report_received_at"] = _ts(d, 23, 30, delay)
                raw_records.append(dup)
                dq_injections.append({"kind": "duplicate_conflict" if conflicting else "duplicate",
                                      "stream_id": stream_id, "date": d.isoformat()})

    sites_meta = {
        s.site_id: {"site_name": s.name, "region": s.region, "synthetic": True}
        for s in SITES
    }
    for r in raw_records:
        meta = sites_meta[r["site_id"]]
        r["site_name"] = meta["site_name"]
        r["region"] = meta["region"]

    fingerprint = dataset_fingerprint(raw_records)
    manifest = {
        "synthetic": True,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "start": dates[0].isoformat(),
        "end": dates[-1].isoformat(),
        "days": days,
        "warmup_days": WARMUP_DAYS,
        "sites": len(SITES),
        "streams": sum(len(s.streams) for s in SITES),
        "raw_records": len(raw_records),
        "fingerprint_sha256": fingerprint,
        "notice": "SYNTHETIC DATA — fictional locations and events for demonstration and evaluation only.",
    }
    labels = {
        "WARNING": "Ground-truth labels for EVALUATION ONLY. Never use as detector input.",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "dataset_fingerprint_sha256": fingerprint,
        "streams": sorted({f"{s.site_id}:{st.service_type}" for s in SITES for st in s.streams}),
        "events": [e.to_label() for e in sorted(events, key=lambda e: (e.start, e.stream_id))],
        "dq_injections": dq_injections,
    }
    return GeneratedDataset(raw_records=raw_records, sites=sites_meta, labels=labels, manifest=manifest)


def _stamp_event_context(rng: random.Random, ev: Event, site: SiteSpec,
                         context: Dict[Tuple[str, date], Dict[str, Any]]) -> None:
    """Write the observable context that accompanies an event (if any)."""
    d = ev.start
    while d <= ev.end:
        entry = context.get((site.site_id, d))
        if entry is not None:
            if ev.event_class == "ACCESS_GAP":
                if ev.subtype == "weather":
                    entry["weather"] = max(entry["weather"], rng.choice((2, 3, 3)))
                elif ev.subtype == "transport":
                    entry["transport"] = True
                elif ev.subtype == "service_outage":
                    entry["outage"] = True
            elif ev.event_class == "AMBIGUOUS" and rng.random() < 0.35:
                if rng.random() < 0.5:
                    entry["weather"] = max(entry["weather"], 1)
                else:
                    entry["transport"] = True
            elif ev.event_class == "NORMAL_VARIATION" and rng.random() < 0.2:
                entry["weather"] = max(entry["weather"], 1)
        d += timedelta(days=1)


def _real_effect(ev: Event, d: date) -> float:
    """Multiplier on real-world demand for events that change actual activity."""
    if ev.event_class == "ACCESS_GAP":
        magnitude = ev.magnitude or 0.5
        ramp = (d == ev.start) and magnitude < 1.0 and (ev.start.toordinal() % 3 == 0)
        return 1.0 - (magnitude / 2 if ramp else magnitude)
    if ev.event_class in ("AMBIGUOUS", "NORMAL_VARIATION"):
        return 1.0 - (ev.magnitude or 0.0)
    return 1.0  # reporting failures do not change real-world activity


def _apply_reporting_effect(rng: random.Random, ev: Event, record: Dict[str, Any], true_count: int,
                            last_good_seen: Optional[str], last_recorded: Optional[int], d: date, t: int) -> bool:
    """Corrupt the *reported* record. Returns False when no record is emitted."""
    frozen = last_good_seen or record["feed_last_seen"]
    if ev.event_class == "AMBIGUOUS":
        if rng.random() < 0.45:
            record["feed_heartbeat_received"] = False
        if record["batches_expected"] and rng.random() < 0.6:
            ratio = rng.uniform(0.6, 0.9)
            record["batches_received"] = max(1, int(round(record["batches_expected"] * ratio)))
            record["observed_count"] = int(round(true_count * ratio))
        return True

    sub = ev.subtype
    if sub == "dropout":
        if rng.random() < 0.6:
            return False
        record.update(observed_count=None, feed_heartbeat_received=False, batches_received=0,
                      feed_last_seen=frozen, report_received_at=_ts(d, 23, 50))
    elif sub == "zero_fill":
        record.update(observed_count=0, feed_heartbeat_received=False, batches_received=0,
                      feed_last_seen=frozen, report_received_at=_ts(d, 23, 50))
    elif sub == "stale_flatline":
        value = last_recorded if last_recorded is not None else true_count
        record.update(observed_count=value, feed_last_seen=frozen, report_received_at=_ts(d, 23, 55))
    elif sub == "partial_batches":
        if record["batches_expected"]:
            lost = ev.magnitude or 0.5
            kept = max(0, int(round(record["batches_expected"] * (1 - lost))))
            record["batches_received"] = kept
            record["observed_count"] = int(round(true_count * kept / record["batches_expected"]))
    elif sub == "covert":
        record["observed_count"] = int(round(true_count * (1 - (ev.magnitude or 0.5))))
    return True


def dataset_fingerprint(raw_records: Sequence[Dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for r in raw_records:
        digest.update(json.dumps(r, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
