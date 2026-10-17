"""Evaluation harness: SILENCE ENGINE versus simpler baselines on SYNTHETIC data.

This is the ONLY module (besides the generator that writes them) that reads
ground-truth labels. Labels are used to (a) tune detection thresholds on the
training period and (b) score the held-out test period. They are never passed
to the pipeline.

Methods compared
  naive     flags large percentage drops vs. the median of the last 4 same
            weekdays; missing data reads as zero; no schedule, feed-health or
            context reasoning; every alert is called a service decline.
  robust    the engine's seasonal baseline + robust z + CUSUM + schedule
            awareness, but with feed-health checks and triage switched off
            (ablation: shows what the data-quality and context layers add).
  engine    full SILENCE ENGINE.

Protocol
  * warm-up: the first 8 weeks only build history (no events are scored)
  * train:   the next ~45% of weeks — thresholds are chosen here (grid search)
  * test:    the remaining weeks — all headline numbers come from here
  Events belong to the period containing their start date.

Detecting a decline and explaining it are scored separately.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import ENGINE_VERSION, pipeline, stats
from .detection import DetectionParams
from .ingest import Observation
from .triage import ACCESS, ACTIONABLE, EXPECTED, REPORTING, UNCERTAIN, TriageParams

ALERTABLE = ("ACCESS_GAP", "REPORTING_FAILURE", "AMBIGUOUS")
BENIGN = ("SCHEDULED_CLOSURE", "NORMAL_VARIATION")
TRUTH_ROWS = ("ACCESS_GAP", "REPORTING_FAILURE", "AMBIGUOUS", "SCHEDULED_CLOSURE", "NORMAL_VARIATION", "NO_EVENT")
PRED_COLS = (ACCESS, REPORTING, UNCERTAIN, EXPECTED, "NOT_FLAGGED")
CORRECT = {"ACCESS_GAP": ACCESS, "REPORTING_FAILURE": REPORTING, "AMBIGUOUS": UNCERTAIN}
SERVICE_GAP_PRED = (ACCESS, UNCERTAIN)

ENGINE_GRID = {
    "z_threshold": (2.5, 3.0, 3.5),
    "min_shortfall": (0.15, 0.25, 0.35),
    "min_persistence": (1, 2, 3),
}
NAIVE_GRID = {
    "drop_threshold": (0.3, 0.4, 0.5, 0.6, 0.7),
    "min_persistence": (1, 2, 3),
}
MISSINGNESS_LEVELS = (0.0, 0.05, 0.10, 0.20)
METHOD_LABELS = {
    "naive": "Naive % drop baseline",
    "robust": "Seasonal detector without feed-health/context (ablation)",
    "engine": "SILENCE ENGINE (full)",
}


# ----------------------------------------------------------------------------
# Labels and protocol
# ----------------------------------------------------------------------------

def load_labels(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return normalise_labels(json.load(fh))


def normalise_labels(data: dict) -> dict:
    """Return a copy with parsed event dates (never mutates the caller's labels)."""
    out = dict(data)
    events = []
    for e in data.get("events", []):
        e = {k: v for k, v in e.items() if not k.startswith("_")}
        e["_start"] = date.fromisoformat(e["start"])
        e["_end"] = date.fromisoformat(e["end"])
        events.append(e)
    out["events"] = events
    return out


@dataclass(frozen=True)
class Protocol:
    start: date
    warmup_end: date
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def period_of(self, d: date) -> Optional[str]:
        if self.train_start <= d <= self.train_end:
            return "train"
        if self.test_start <= d <= self.test_end:
            return "test"
        return None

    def bounds(self, period: str) -> Tuple[date, date]:
        return (self.train_start, self.train_end) if period == "train" else (self.test_start, self.test_end)

    def to_dict(self) -> dict:
        return {k: v.isoformat() for k, v in asdict(self).items()}


def make_protocol(start: date, end: date, warmup_days: int = 56, train_share: float = 0.45) -> Protocol:
    warmup_end = start + timedelta(days=warmup_days - 1)
    remaining_weeks = ((end - warmup_end).days) // 7
    train_weeks = max(4, int(round(remaining_weeks * train_share)))
    train_start = warmup_end + timedelta(days=1)
    train_end = train_start + timedelta(days=7 * train_weeks - 1)
    return Protocol(start, warmup_end, train_start, train_end, train_end + timedelta(days=1), end)


# ----------------------------------------------------------------------------
# Predictions (common shape for every method)
# ----------------------------------------------------------------------------

@dataclass
class Prediction:
    stream_id: str
    onset: date
    end: date
    alert_date: Optional[date]
    category: str
    actionable: bool
    evidence_band: Optional[str] = None
    case_id: Optional[str] = None


def predictions_from_cases(cases: Iterable[dict]) -> List[Prediction]:
    out = []
    for c in cases:
        out.append(Prediction(
            stream_id=c["stream_id"], onset=date.fromisoformat(c["onset"]), end=date.fromisoformat(c["end"]),
            alert_date=date.fromisoformat(c["alert_date"]) if c.get("alert_date") else None,
            category=c["category"], actionable=bool(c["actionable"]), evidence_band=c.get("evidence_band"),
            case_id=c.get("case_id")))
    return out


@dataclass(frozen=True)
class NaiveParams:
    drop_threshold: float = 0.5
    min_persistence: int = 2
    lookback_weeks: int = 4


def naive_predictions(observations: Sequence[Observation], params: NaiveParams,
                      end_date: Optional[date] = None) -> List[Prediction]:
    """Simple percentage-drop detector with no context or feed-health analysis."""
    totals: Dict[str, Dict[date, int]] = {}
    first: Dict[str, date] = {}
    last = end_date
    for o in observations:
        per = totals.setdefault(o.stream_id, {})
        per[o.service_date] = per.get(o.service_date, 0) + (o.observed_count or 0)  # duplicates summed, missing -> 0
        if o.stream_id not in first or o.service_date < first[o.stream_id]:
            first[o.stream_id] = o.service_date
        if end_date is None and (last is None or o.service_date > last):
            last = o.service_date
    preds: List[Prediction] = []
    if last is None:
        return preds
    for sid, per in totals.items():
        d = first[sid]
        run: List[date] = []
        while d <= last:
            hist = [per.get(d - timedelta(days=7 * w), 0) for w in range(1, params.lookback_weeks + 1)
                    if d - timedelta(days=7 * w) >= first[sid]]
            expected = stats.median(hist) if len(hist) >= 2 else None
            value = per.get(d, 0)
            if expected is None or expected < 1:
                d += timedelta(days=1)
                continue  # nothing expected: neither flags nor breaks a run
            if (expected - value) / expected >= params.drop_threshold:
                run.append(d)
            else:
                _naive_close(sid, run, params, preds)
                run = []
            d += timedelta(days=1)
        _naive_close(sid, run, params, preds)
    return preds


def _naive_close(sid: str, run: List[date], params: NaiveParams, out: List[Prediction]) -> None:
    if len(run) >= params.min_persistence:
        out.append(Prediction(sid, run[0], run[-1], run[params.min_persistence - 1], ACCESS, True))


# ----------------------------------------------------------------------------
# Matching and metrics
# ----------------------------------------------------------------------------

def _overlaps(p: Prediction, e: dict, tol: int = 1) -> int:
    lo = max(p.onset, e["_start"] - timedelta(days=tol))
    hi = min(p.end, e["_end"] + timedelta(days=tol))
    return (hi - lo).days + 1 if hi >= lo else 0


def score(preds: Sequence[Prediction], events: Sequence[dict], protocol: Protocol, period: str,
          stream_days: Dict[str, int], tiers: Optional[Dict[str, str]] = None) -> dict:
    """Event-level detection and explanation metrics for one period."""
    p_lo, p_hi = protocol.bounds(period)
    period_events = [e for e in events if p_lo <= e["_start"] <= p_hi]
    by_stream: Dict[str, List[Prediction]] = {}
    for p in preds:
        by_stream.setdefault(p.stream_id, []).append(p)
    # Actionable alerts raised in this period.
    alerts = [p for p in preds if p.actionable and p.alert_date and p_lo <= p.alert_date <= p_hi]

    event_rows = []
    for e in period_events:
        cands = [p for p in by_stream.get(e["stream_id"], []) if _overlaps(p, e)]
        act = sorted([p for p in cands if p.actionable], key=lambda p: (p.alert_date or p.onset))
        if act:
            pred = act[0]
            category = pred.category
        elif cands:
            pred, category = cands[0], cands[0].category
        else:
            pred, category = None, "NOT_FLAGGED"
        delay = None
        if act and e["class"] in ALERTABLE and pred.alert_date:
            delay = max(0, (pred.alert_date - e["_start"]).days)
        event_rows.append({"event": e, "pred": pred, "category": category, "detected": bool(act), "delay": delay})

    # Each alert -> truth class of the event it overlaps most (or NO_EVENT).
    stream_events: Dict[str, List[dict]] = {}
    for e in events:
        stream_events.setdefault(e["stream_id"], []).append(e)
    alert_rows = []
    for a in alerts:
        best, best_ov = None, 0
        for e in stream_events.get(a.stream_id, []):
            ov = _overlaps(a, e)
            if ov > best_ov:
                best, best_ov = e, ov
        alert_rows.append({"alert": a, "truth": best["class"] if best else "NO_EVENT"})

    return _metrics(event_rows, alert_rows, stream_days, period_events, tiers)


def _rate(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def _ci(n: int, d: int) -> List[Optional[float]]:
    lo, hi = stats.wilson_interval(n, d)
    return [stats.round_or_none(lo, 4), stats.round_or_none(hi, 4)]


def _metrics(event_rows: List[dict], alert_rows: List[dict], stream_days: Dict[str, int],
             period_events: List[dict], tiers: Optional[Dict[str, str]]) -> dict:
    alertable = [r for r in event_rows if r["event"]["class"] in ALERTABLE]
    benign = [r for r in event_rows if r["event"]["class"] in BENIGN]
    detected = [r for r in alertable if r["detected"]]
    true_alerts = [r for r in alert_rows if r["truth"] in ALERTABLE]
    false_alerts = [r for r in alert_rows if r["truth"] not in ALERTABLE]
    stream_weeks = sum(stream_days.values()) / 7.0
    recall = _rate(len(detected), len(alertable))
    precision = _rate(len(true_alerts), len(alert_rows))
    f1 = stats.f1(precision, recall)

    matrix = {row: {col: 0 for col in PRED_COLS} for row in TRUTH_ROWS}
    for r in event_rows:
        matrix[r["event"]["class"]][r["category"] if r["category"] in PRED_COLS else "NOT_FLAGGED"] += 1
    for r in alert_rows:
        if r["truth"] == "NO_EVENT":
            matrix["NO_EVENT"][r["alert"].category] += 1

    correct = [r for r in detected if r["category"] == CORRECT[r["event"]["class"]]]
    per_class = []
    for truth_class, cat in CORRECT.items():
        truth_n = sum(1 for r in alertable if r["event"]["class"] == truth_class)
        pred_n = sum(1 for r in alert_rows if r["alert"].category == cat)
        hit_alerts = sum(1 for r in alert_rows if r["alert"].category == cat and r["truth"] == truth_class)
        hit_events = sum(1 for r in alertable if r["event"]["class"] == truth_class and r["category"] == cat)
        per_class.append({
            "truth_class": truth_class, "category": cat, "events": truth_n, "alerts_with_category": pred_n,
            "precision": _rate(hit_alerts, pred_n), "precision_ci": _ci(hit_alerts, pred_n),
            "recall": _rate(hit_events, truth_n), "recall_ci": _ci(hit_events, truth_n),
        })
    benign_ok = [r for r in benign if not r["detected"]]
    rf_detected = [r for r in detected if r["event"]["class"] == "REPORTING_FAILURE"]
    ag_detected = [r for r in detected if r["event"]["class"] == "ACCESS_GAP"]
    non_amb = [r for r in detected if r["event"]["class"] != "AMBIGUOUS"]
    delays = [r["delay"] for r in detected if r["delay"] is not None]
    service_gap_false = [r for r in alert_rows if r["alert"].category in SERVICE_GAP_PRED
                         and r["truth"] not in ("ACCESS_GAP", "AMBIGUOUS")]

    out = {
        "detection": {
            "alertable_events": len(alertable), "detected": len(detected),
            "recall": recall, "recall_ci": _ci(len(detected), len(alertable)),
            "alerts": len(alert_rows), "true_alerts": len(true_alerts),
            "precision": precision, "precision_ci": _ci(len(true_alerts), len(alert_rows)),
            "f1": stats.round_or_none(f1, 4),
            "false_alerts": len(false_alerts),
            "false_alerts_per_100_stream_weeks": round(100 * len(false_alerts) / stream_weeks, 2) if stream_weeks else None,
            "false_alerts_by_truth": {k: sum(1 for r in false_alerts if r["truth"] == k)
                                      for k in ("SCHEDULED_CLOSURE", "NORMAL_VARIATION", "NO_EVENT")},
            "false_service_gap_alerts": len(service_gap_false),
            "false_service_gap_alerts_per_100_stream_weeks":
                round(100 * len(service_gap_false) / stream_weeks, 2) if stream_weeks else None,
            "benign_events": len(benign), "benign_events_not_alerted": len(benign_ok),
            "benign_handled_rate": _rate(len(benign_ok), len(benign)),
            "delay_days": stats.summarize(delays),
            "delay_by_class": {cls: stats.summarize([r["delay"] for r in detected if r["event"]["class"] == cls
                                                     and r["delay"] is not None]) for cls in ALERTABLE},
            "recall_by_class": {cls: {
                "events": sum(1 for r in alertable if r["event"]["class"] == cls),
                "detected": sum(1 for r in detected if r["event"]["class"] == cls),
                "recall": _rate(sum(1 for r in detected if r["event"]["class"] == cls),
                                sum(1 for r in alertable if r["event"]["class"] == cls)),
            } for cls in ALERTABLE},
            "stream_weeks": round(stream_weeks, 1),
        },
        "explanation": {
            "accuracy_given_detection": _rate(len(correct), len(detected)),
            "accuracy_ci": _ci(len(correct), len(detected)),
            "correct": len(correct), "detected": len(detected),
            "per_class": per_class,
            "reporting_failures_called_access_gaps": _rate(sum(1 for r in rf_detected if r["category"] == ACCESS), len(rf_detected)),
            "access_gaps_called_reporting": _rate(sum(1 for r in ag_detected if r["category"] == REPORTING), len(ag_detected)),
            "abstention_rate_non_ambiguous": _rate(sum(1 for r in non_amb if r["category"] == UNCERTAIN), len(non_amb)),
            "confusion": {"rows": list(TRUTH_ROWS), "cols": list(PRED_COLS),
                          "matrix": [[matrix[r][c] for c in PRED_COLS] for r in TRUTH_ROWS]},
        },
        "subtype_detail": _subtype_detail(event_rows),
    }
    if tiers:
        out["volume_tiers"] = _tier_metrics(event_rows, alert_rows, stream_days, tiers)
    return out


def _subtype_detail(event_rows: List[dict]) -> List[dict]:
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for r in event_rows:
        groups.setdefault((r["event"]["class"], r["event"]["subtype"]), []).append(r)
    out = []
    for (cls, sub), rows in sorted(groups.items()):
        cats: Dict[str, int] = {}
        for r in rows:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        out.append({"class": cls, "subtype": sub, "events": len(rows),
                    "detected": sum(1 for r in rows if r["detected"]), "predicted": cats})
    return out


def _tier_metrics(event_rows: List[dict], alert_rows: List[dict], stream_days: Dict[str, int],
                  tiers: Dict[str, str]) -> List[dict]:
    out = []
    for tier in ("high", "medium", "low"):
        streams = {s for s, t in tiers.items() if t == tier}
        if not streams:
            continue
        ev = [r for r in event_rows if r["event"]["stream_id"] in streams and r["event"]["class"] in ALERTABLE]
        det = [r for r in ev if r["detected"]]
        al = [r for r in alert_rows if r["alert"].stream_id in streams]
        fa = [r for r in al if r["truth"] not in ALERTABLE]
        sw = sum(d for s, d in stream_days.items() if s in streams) / 7.0
        corr = [r for r in det if r["category"] == CORRECT[r["event"]["class"]]]
        out.append({
            "tier": tier, "streams": sorted(streams), "alertable_events": len(ev), "detected": len(det),
            "recall": _rate(len(det), len(ev)), "alerts": len(al), "precision": _rate(len(al) - len(fa), len(al)),
            "false_alerts_per_100_stream_weeks": round(100 * len(fa) / sw, 2) if sw else None,
            "explanation_accuracy": _rate(len(corr), len(det)),
        })
    return out


# ----------------------------------------------------------------------------
# Running methods
# ----------------------------------------------------------------------------

def _stream_days(prep: pipeline.Prepared, protocol: Protocol, period: str) -> Dict[str, int]:
    lo, hi = protocol.bounds(period)
    out = {}
    for sid, sd in prep.streams.items():
        first = max(sd.first_date, lo)
        last = min(sd.last_date, hi)
        if last >= first:
            out[sid] = (last - first).days + 1
    return out


def volume_tiers(prep: pipeline.Prepared, protocol: Protocol) -> Dict[str, str]:
    """Tier streams by median trusted daily count in the TRAINING period (no labels used)."""
    tiers = {}
    for sid, sd in prep.streams.items():
        vals = [r.observed for r in sd.days if r.trusted and protocol.train_start <= r.date <= protocol.train_end]
        if not vals:
            vals = [r.observed for r in sd.days if r.trusted]
        level = stats.median(vals) if vals else 0
        tiers[sid] = "high" if level >= 100 else "medium" if level >= 30 else "low"
    return tiers


def run_engine(prep: pipeline.Prepared, params: DetectionParams, simple: bool = False) -> List[Prediction]:
    result = pipeline.analyze_prepared(prep, params, TriageParams(), simple_triage=simple)
    return predictions_from_cases(result.cases)


def _objective(m: dict) -> Tuple[float, float, float]:
    d = m["detection"]
    return (d["f1"] or 0.0, m["explanation"]["accuracy_given_detection"] or 0.0,
            -(d["false_alerts_per_100_stream_weeks"] or 0.0))


def tune(method: str, preps: Dict[str, pipeline.Prepared], observations: Sequence[Observation],
         events: Sequence[dict], protocol: Protocol) -> Tuple[Any, List[dict]]:
    grid_rows = []
    best, best_key = None, None
    if method == "naive":
        sd = _stream_days(preps["engine"], protocol, "train")
        for thr in NAIVE_GRID["drop_threshold"]:
            for pers in NAIVE_GRID["min_persistence"]:
                params = NaiveParams(drop_threshold=thr, min_persistence=pers)
                m = score(naive_predictions(observations, params), events, protocol, "train", sd)
                key = _objective(m)
                grid_rows.append({"params": asdict(params), "f1": m["detection"]["f1"],
                                  "recall": m["detection"]["recall"], "precision": m["detection"]["precision"]})
                if best_key is None or key > best_key:
                    best, best_key = params, key
        return best, grid_rows
    prep = preps["engine"] if method == "engine" else preps["robust"]
    sd = _stream_days(prep, protocol, "train")
    base = DetectionParams(use_feed_health=(method == "engine"))
    for z in ENGINE_GRID["z_threshold"]:
        for ms in ENGINE_GRID["min_shortfall"]:
            for pers in ENGINE_GRID["min_persistence"]:
                params = replace(base, z_threshold=z, min_shortfall=ms, min_persistence=pers)
                m = score(run_engine(prep, params, simple=(method == "robust")), events, protocol, "train", sd)
                key = _objective(m)
                grid_rows.append({"params": {"z_threshold": z, "min_shortfall": ms, "min_persistence": pers},
                                  "f1": m["detection"]["f1"], "recall": m["detection"]["recall"],
                                  "precision": m["detection"]["precision"],
                                  "explanation_accuracy": m["explanation"]["accuracy_given_detection"]})
                if best_key is None or key > best_key:
                    best, best_key = params, key
    return best, grid_rows


def _drop_rows(observations: Sequence[Observation], protocol: Protocol, share: float, seed: int) -> List[Observation]:
    """Remove a share of test-period records. The choice hashes each record's key, so it does
    not depend on the order the records were loaded in (generator vs database order)."""
    if share <= 0:
        return list(observations)
    out = []
    for o in observations:
        if protocol.test_start <= o.service_date <= protocol.test_end:
            key = f"{seed}:{o.stream_id}:{o.service_date.isoformat()}".encode("utf-8")
            u = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2**64
            if u < share:
                continue
        out.append(o)
    return out


def _evidence_bands(preds: Sequence[Prediction], events: Sequence[dict], protocol: Protocol) -> List[dict]:
    lo, hi = protocol.bounds("test")
    rows: Dict[str, List[bool]] = {}
    for e in events:
        if e["class"] not in ALERTABLE or not (lo <= e["_start"] <= hi):
            continue
        act = sorted([p for p in preds if p.stream_id == e["stream_id"] and p.actionable and _overlaps(p, e)],
                     key=lambda p: p.alert_date or p.onset)
        if act:
            rows.setdefault(act[0].evidence_band or "n/a", []).append(act[0].category == CORRECT[e["class"]])
    return [{"band": b, "events": len(v), "correct": sum(v), "accuracy": _rate(sum(v), len(v))}
            for b, v in sorted(rows.items(), key=lambda kv: ("Strong", "Moderate", "Limited", "n/a").index(kv[0]))]


def operational_replay(observations: Sequence[Observation], sites: Dict[str, dict], params: DetectionParams,
                       events: Sequence[dict], protocol: Protocol, final_preds: Sequence[Prediction],
                       grace_days: int, history_days: int = 63, recent_days: int = 10) -> dict:
    """Day-by-day replay using only records that had *arrived* by each evening.

    Shows how reporting delays affect alert timing and how many alerts are
    transient (raised on data that later arrived and cleared them).
    """
    by_date: Dict[date, List[Observation]] = {}
    for o in observations:
        by_date.setdefault(o.service_date, []).append(o)
    first_seen: Dict[str, Tuple[date, str]] = {}  # event_id -> (T, category)
    transient_keys = set()
    final_by_stream: Dict[str, List[Prediction]] = {}
    for p in final_preds:
        if p.actionable:
            final_by_stream.setdefault(p.stream_id, []).append(p)
    test_events = [e for e in events if e["class"] in ALERTABLE and protocol.test_start <= e["_start"] <= protocol.test_end]
    T = protocol.test_start
    runs = 0
    while T <= protocol.test_end:
        visible_at = datetime.combine(T, dtime(23, 59, 59), tzinfo=timezone.utc)
        assess_end = T - timedelta(days=grace_days)
        window: List[Observation] = []
        d = T - timedelta(days=history_days)
        while d <= assess_end:
            window.extend(by_date.get(d, ()))
            d += timedelta(days=1)
        prep = pipeline.prepare(window, sites, end_date=assess_end, visible_at=visible_at, params=params)
        result = pipeline.analyze_prepared(prep, params, TriageParams())
        runs += 1
        recent_lo = assess_end - timedelta(days=recent_days)
        for p in predictions_from_cases(result.cases):
            if not p.actionable or p.end < recent_lo or not p.alert_date:
                continue
            for e in test_events:
                if e["event_id"] not in first_seen and e["stream_id"] == p.stream_id and _overlaps(p, e):
                    first_seen[e["event_id"]] = (T, p.category)
            if not any(fp.onset <= p.end and p.onset <= fp.end for fp in final_by_stream.get(p.stream_id, [])):
                transient_keys.add((p.stream_id, p.onset))
        T += timedelta(days=1)
    delays = [(first_seen[e["event_id"]][0] - e["_start"]).days for e in test_events if e["event_id"] in first_seen]
    correct = sum(1 for e in test_events if e["event_id"] in first_seen and first_seen[e["event_id"]][1] == CORRECT[e["class"]])
    return {
        "grace_days": grace_days, "replay_days": runs, "alertable_events": len(test_events),
        "detected": len(first_seen), "recall": _rate(len(first_seen), len(test_events)),
        "operational_delay_days": stats.summarize(delays),
        "category_correct_at_first_alert": _rate(correct, len(first_seen)),
        "transient_alerts": len(transient_keys),
    }


def evaluate(observations: Sequence[Observation], sites: Dict[str, dict], labels: dict,
             include_replay: bool = True, progress=None) -> dict:
    """Full evaluation. Returns a JSON-serialisable report."""
    t0 = time.time()
    labels = normalise_labels(labels)
    label_streams = set(labels.get("streams", []))
    obs = [o for o in observations if o.stream_id in label_streams]
    excluded = len(observations) - len(obs)
    events = [e for e in labels["events"]]
    start = min(o.service_date for o in obs)
    end = max(o.service_date for o in obs)
    protocol = make_protocol(start, end)

    def step(msg: str) -> None:
        if progress:
            progress(msg)

    step("preparing data")
    preps = {
        "engine": pipeline.prepare(obs, sites, params=DetectionParams(use_feed_health=True)),
        "robust": pipeline.prepare(obs, sites, params=DetectionParams(use_feed_health=False)),
    }
    tiers = volume_tiers(preps["engine"], protocol)
    step("tuning thresholds on training period")
    tuned: Dict[str, Any] = {}
    grids: Dict[str, List[dict]] = {}
    for method in ("naive", "robust", "engine"):
        tuned[method], grids[method] = tune(method, preps, obs, events, protocol)

    step("scoring held-out test period")
    preds = {
        "naive": naive_predictions(obs, tuned["naive"]),
        "robust": run_engine(preps["robust"], tuned["robust"], simple=True),
        "engine": run_engine(preps["engine"], tuned["engine"]),
    }
    results = {}
    for period in ("train", "test"):
        sd = _stream_days(preps["engine"], protocol, period)
        results[period] = {m: score(preds[m], events, protocol, period, sd, tiers) for m in preds}

    step("robustness: incomplete reporting")
    robustness = []
    for share in MISSINGNESS_LEVELS:
        degraded = _drop_rows(obs, protocol, share, seed=20260 + int(share * 100))
        row = {"missing_share": share, "methods": {}}
        prep_e = pipeline.prepare(degraded, sites, params=tuned["engine"])
        prep_r = pipeline.prepare(degraded, sites, params=tuned["robust"])
        sd = _stream_days(prep_e, protocol, "test")
        for m, p in (("naive", naive_predictions(degraded, tuned["naive"])),
                     ("robust", run_engine(prep_r, tuned["robust"], simple=True)),
                     ("engine", run_engine(prep_e, tuned["engine"]))):
            s = score(p, events, protocol, "test", sd)
            ag = next(pc for pc in s["explanation"]["per_class"] if pc["truth_class"] == "ACCESS_GAP")
            row["methods"][m] = {
                "recall": s["detection"]["recall"],
                "access_gap_recall": ag["recall"], "access_gap_precision": ag["precision"],
                "false_service_gap_alerts_per_100_stream_weeks": s["detection"]["false_service_gap_alerts_per_100_stream_weeks"],
                "false_alerts_per_100_stream_weeks": s["detection"]["false_alerts_per_100_stream_weeks"],
                "explanation_accuracy": s["explanation"]["accuracy_given_detection"],
                "reporting_alerts": sum(1 for x in p if x.actionable and x.category == REPORTING
                                        and x.alert_date and protocol.test_start <= x.alert_date <= protocol.test_end),
            }
        robustness.append(row)

    replay = []
    if include_replay:
        step("operational replay with reporting delays")
        for grace in (0, 1):
            replay.append(operational_replay(obs, sites, tuned["engine"], events, protocol, preds["engine"], grace))

    counts: Dict[str, Dict[str, int]] = {"train": {}, "test": {}}
    for e in events:
        p = protocol.period_of(e["_start"])
        if p:
            counts[p][e["class"]] = counts[p].get(e["class"], 0) + 1

    report = {
        "synthetic": True,
        "notice": "All results are computed on SYNTHETIC data with known ground truth. They show internal consistency "
                  "and robustness of the method, not real-world accuracy.",
        "engine_version": ENGINE_VERSION,
        "dataset_fingerprint": labels.get("dataset_fingerprint_sha256"),
        "protocol": protocol.to_dict(),
        "label_counts": counts,
        "observations_used": len(obs), "observations_excluded_unlabelled": excluded,
        "volume_tiers": tiers,
        "methods": METHOD_LABELS,
        "tuned_params": {
            "naive": asdict(tuned["naive"]),
            "robust": {k: getattr(tuned["robust"], k) for k in ENGINE_GRID},
            "engine": {k: getattr(tuned["engine"], k) for k in ENGINE_GRID},
        },
        "engine_params_full": tuned["engine"].to_dict(),
        "grid": grids,
        "train": results["train"],
        "test": results["test"],
        "evidence_bands": _evidence_bands(preds["engine"], events, protocol),
        "robustness_missingness": robustness,
        "operational_replay": replay,
    }
    report["results_sha256"] = results_hash(report)
    report["duration_seconds"] = round(time.time() - t0, 2)
    return report


def results_hash(report: dict) -> str:
    """Hash of the metric content only (no timings), for cross-platform comparison."""
    content = {k: v for k, v in report.items() if k not in ("duration_seconds", "results_sha256", "generated_at", "platform")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode("utf-8")).hexdigest()
