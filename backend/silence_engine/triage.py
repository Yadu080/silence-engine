"""Triage: turn a detected episode into an investigation hypothesis.

Categories (hypotheses for human verification, never causal conclusions):
  POTENTIAL_ACCESS_GAP  activity well below expectation while the feed looks healthy
  REPORTING_PROBLEM     the apparent decline is plausibly an artefact of the data feed
  EXPECTED_VARIATION    explained by the schedule or within historical variation
  UNCERTAIN             evidence insufficient or conflicting — investigate

The rules are deterministic and documented in docs/METHODOLOGY.md. Each
evidence item records which hypotheses it supports or contradicts, giving an
"analysis of competing hypotheses" view in the case page.

Two scores are produced. Neither is a probability:
  priority (0-100)          a ranking heuristic for the investigation queue
  evidence_score (0-100)    how much and how consistent the evidence is
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from . import stats
from .detection import DayAssessment, Episode

ACCESS = "POTENTIAL_ACCESS_GAP"
REPORTING = "REPORTING_PROBLEM"
EXPECTED = "EXPECTED_VARIATION"
UNCERTAIN = "UNCERTAIN"
CATEGORIES = (ACCESS, REPORTING, UNCERTAIN, EXPECTED)
ACTIONABLE = (ACCESS, REPORTING, UNCERTAIN)
CATEGORY_LABELS = {
    ACCESS: "Potential service-access gap",
    REPORTING: "Likely reporting problem",
    UNCERTAIN: "Uncertain — investigate",
    EXPECTED: "Expected variation",
}
HYPOTHESES = {
    "ACCESS": "Service-access gap (real reduction in service delivery)",
    "REPORTING": "Data-reporting failure (activity happened but was not recorded correctly)",
    "NORMAL": "Normal variation or scheduled change",
}
CATEGORY_WEIGHT = {ACCESS: 1.0, UNCERTAIN: 0.85, REPORTING: 0.7, EXPECTED: 0.15}


@dataclass(frozen=True)
class TriageParams:
    strong_shortfall: float = 0.35
    strong_z: float = -4.0
    moderate_shortfall: float = 0.20
    moderate_z: float = -2.5
    feed_failed_frac: float = 0.6
    feed_degraded_frac: float = 0.2
    precedent_threshold: float = 0.10
    min_history: int = 4
    low_volume: float = 20.0
    peer_decline_frac: float = 0.2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StreamContext:
    stream_id: str
    site_id: str
    site_name: str
    region: Optional[str]
    service_type: str
    days: List[DayAssessment]
    index_by_date: Dict[date, int]


def make_context(stream_id: str, site_id: str, site_name: str, region: Optional[str], service_type: str,
                 days: List[DayAssessment]) -> StreamContext:
    return StreamContext(stream_id, site_id, site_name, region, service_type, days,
                         {a.date: i for i, a in enumerate(days)})


def _abbr(service_type: str) -> str:
    parts = [p for p in service_type.split("_") if p]
    code = "".join(p[0] for p in parts).upper()[:4]
    return code or "SVC"


def case_id_for(site_id: str, service_type: str, onset: date) -> str:
    return f"{site_id}-{_abbr(service_type)}-{onset.strftime('%Y%m%d')}"


def _fmt_date(d: date) -> str:
    return d.strftime("%d %b %Y").lstrip("0")


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{round(100 * x)}%"


def _evidence(text: str, kind: str, supports: Sequence[str] = (), contradicts: Sequence[str] = (),
              strength: str = "moderate") -> dict:
    return {"text": text, "kind": kind, "supports": list(supports), "contradicts": list(contradicts),
            "strength": strength}


# ----------------------------------------------------------------------------
# Feature extraction
# ----------------------------------------------------------------------------

def _window_features(ctx: StreamContext, ep: Episode, tp: TriageParams) -> dict:
    days = ctx.days[ep.start_index: ep.end_index + 1]
    open_days = [a for a in days if a.scheduled_open]
    status_counts: Dict[str, int] = {}
    for a in days:
        status_counts[a.status] = status_counts.get(a.status, 0) + 1
    trusted = [a for a in open_days if a.status in ("ok", "low") and a.expected is not None and a.observed is not None]
    exp_sum = sum(a.expected for a in trusted)
    obs_sum = sum(a.observed for a in trusted)
    var_sum = sum((a.sigma or 1.0) ** 2 for a in trusted)
    trusted_frac = (1 - obs_sum / exp_sum) if exp_sum >= 1 else None
    z_agg = ((obs_sum - exp_sum) / math.sqrt(var_sum)) if trusted and var_sum > 0 else None
    problem = [a for a in open_days if a.status in ("missing", "suspect")]
    missing_expected = sum(a.expected for a in problem if a.expected is not None)
    shortfall_units = sum(max(0.0, a.expected - a.observed) for a in trusted)
    expected_total = sum(a.expected for a in open_days if a.expected is not None)
    reported_total = sum(a.observed for a in open_days if a.observed is not None and a.status != "missing")
    hist = [a.n_history for a in open_days if a.status not in ("missing",)] or [a.n_history for a in open_days]
    flag_days: Dict[str, int] = {}
    for a in open_days:
        for f in set(a.flags):
            flag_days[f] = flag_days.get(f, 0) + 1
    zs = [a.z for a in trusted if a.z is not None]
    mean_expected = (expected_total / len([a for a in open_days if a.expected is not None])) \
        if any(a.expected is not None for a in open_days) else None
    feed_frac = (len(problem) / len(open_days)) if open_days else 0.0
    if feed_frac >= tp.feed_failed_frac:
        feed_status = "FAILED"
    elif feed_frac >= tp.feed_degraded_frac:
        feed_status = "DEGRADED"
    else:
        feed_status = "HEALTHY"
    return {
        "days": days, "open_days": open_days, "status_counts": status_counts, "trusted": trusted,
        "n_open": len(open_days), "n_trusted": len(trusted),
        "n_low": status_counts.get("low", 0), "n_missing": status_counts.get("missing", 0),
        "n_suspect": status_counts.get("suspect", 0), "n_closed_unusual": status_counts.get("closed_unusual", 0),
        "trusted_expected": exp_sum, "trusted_observed": obs_sum, "trusted_frac": trusted_frac, "z_agg": z_agg,
        "min_z": min(zs) if zs else None, "missing_expected": missing_expected, "shortfall_units": shortfall_units,
        "expected_total": expected_total, "reported_total": reported_total,
        "history_median": stats.median(hist) if hist else 0, "flag_days": flag_days,
        "feed_frac": feed_frac, "feed_status": feed_status, "mean_expected": mean_expected,
        "inferred_schedule_days": sum(1 for a in open_days if a.schedule_inferred),
    }


def _context_features(ctx: StreamContext, ep: Episode) -> dict:
    lo = ep.start - timedelta(days=1)
    hi = ep.end + timedelta(days=1)
    rows = [a for a in ctx.days if lo <= a.date <= hi]
    weather = [a.weather for a in rows if a.weather is not None]
    return {
        "window_start": lo.isoformat(), "window_end": hi.isoformat(), "window_days": (hi - lo).days + 1,
        "weather_max": max(weather) if weather else None,
        "severe_weather_days": sum(1 for w in weather if w >= 2),
        "transport_days": sum(1 for a in rows if a.transport),
        "outage_days": sum(1 for a in rows if a.outage),
        "observed_rows": len(rows),
    }


def _peer_features(ctx: StreamContext, ep: Episode, peers: Sequence[StreamContext], tp: TriageParams) -> dict:
    results = []
    for p in peers:
        if p.stream_id == ctx.stream_id:
            continue
        same_site = p.site_id == ctx.site_id
        if not same_site and (not ctx.region or p.region != ctx.region):
            continue
        exp_sum = obs_sum = 0.0
        n = 0
        d = ep.start
        while d <= ep.end:
            i = p.index_by_date.get(d)
            if i is not None:
                a = p.days[i]
                if a.status in ("ok", "low") and a.expected is not None and a.observed is not None:
                    exp_sum += a.expected
                    obs_sum += a.observed
                    n += 1
            d += timedelta(days=1)
        if n >= 2 and exp_sum >= 1:
            frac = 1 - obs_sum / exp_sum
            results.append({"stream_id": p.stream_id, "site_name": p.site_name, "service_type": p.service_type,
                            "same_site": same_site, "shortfall_frac": round(frac, 3),
                            "declining": frac >= tp.peer_decline_frac, "days_compared": n})
    return {
        "compared": len(results),
        "declining": sum(1 for r in results if r["declining"]),
        "peers": results,
    }


def _precedent_rate(ctx: StreamContext, ep: Episode, frac: Optional[float], n_days: int,
                    lookback: int = 120) -> Tuple[Optional[float], int]:
    """Share of earlier windows of ordinary (ok) days with a shortfall at least this large."""
    if frac is None or n_days <= 0:
        return None, 0
    lo = ep.start - timedelta(days=lookback)
    ordinary = [a for a in ctx.days if lo <= a.date < ep.start and a.status == "ok"
                and a.shortfall_frac is not None]
    m = max(1, min(n_days, 7))
    if len(ordinary) < m + 20:
        return None, 0
    windows = 0
    hits = 0
    for i in range(0, len(ordinary) - m + 1):
        mean_frac = sum(a.shortfall_frac for a in ordinary[i:i + m]) / m
        windows += 1
        if mean_frac >= frac:
            hits += 1
    return (hits / windows if windows else None), windows


# ----------------------------------------------------------------------------
# Decision rules
# ----------------------------------------------------------------------------

def _decline_strength(w: dict, tp: TriageParams) -> str:
    frac, z = w["trusted_frac"], w["z_agg"]
    if w["n_trusted"] == 0 or frac is None or z is None:
        return "UNMEASURABLE"
    if frac >= tp.strong_shortfall and z <= tp.strong_z:
        return "STRONG"
    if frac >= tp.moderate_shortfall and z <= tp.moderate_z:
        return "MODERATE"
    if frac >= tp.moderate_shortfall / 2 and w["n_low"] > 0:
        return "WEAK"
    return "NONE"


def _decide(ep: Episode, w: dict, c: dict, precedent: Optional[float], tp: TriageParams) -> Tuple[str, str]:
    """Returns (category, reason_code)."""
    if ep.kind == "closure":
        return EXPECTED, "scheduled_closure"
    decline = _decline_strength(w, tp)
    context_present = (c["severe_weather_days"] > 0 or c["transport_days"] > 0 or c["outage_days"] > 0)
    history_ok = w["history_median"] >= tp.min_history
    feed = w["feed_status"]
    if feed == "FAILED":
        if decline == "STRONG" and w["n_low"] >= 2:
            return UNCERTAIN, "decline_with_failed_feed"
        return REPORTING, "feed_failure"
    if feed == "DEGRADED":
        if decline in ("STRONG", "MODERATE"):
            return UNCERTAIN, "decline_with_degraded_feed"
        if not history_ok:
            return UNCERTAIN, "insufficient_history"
        return REPORTING, "degraded_feed"
    if not history_ok:
        return UNCERTAIN, "insufficient_history"
    if decline == "STRONG":
        return ACCESS, "strong_decline_healthy_feed"
    if decline == "MODERATE":
        if precedent is not None and precedent >= tp.precedent_threshold:
            return EXPECTED, "historical_precedent"
        if context_present:
            return ACCESS, "moderate_decline_with_context"
        return UNCERTAIN, "moderate_decline_no_context"
    return EXPECTED, "within_normal_range"


REASON_TEXT = {
    "scheduled_closure": "The operating schedule lists the service as closed on days it is normally open, so lower activity is expected.",
    "decline_with_failed_feed": "Most days have missing or untrustworthy data, yet the few days with healthy feeds also show a large decline. Both a reporting failure and a real reduction are plausible.",
    "feed_failure": "Most open days in this window have missing or untrustworthy records, so the apparent silence is more likely a reporting problem than a measured decline.",
    "decline_with_degraded_feed": "Recorded activity fell, but the data feed was degraded over the same period. The decline may be partly or wholly a reporting artefact.",
    "insufficient_history": "There are too few comparable past days to estimate expected activity reliably.",
    "degraded_feed": "The feed shows intermittent gaps or doubtful values without a clear decline on healthy days.",
    "strong_decline_healthy_feed": "Activity is far below what comparable past days predict, and the data feed appears healthy on those days.",
    "historical_precedent": "A moderate shortfall of this size has occurred regularly in this stream's recent history without any incident.",
    "moderate_decline_with_context": "Activity is moderately below expectation with a healthy feed, and contextual disruption coincides with the decline.",
    "moderate_decline_no_context": "Activity is moderately below expectation with a healthy feed, but there is no coinciding context and little historical precedent.",
    "within_normal_range": "The shortfall on trustworthy days is within the stream's normal range of variation.",
}


# ----------------------------------------------------------------------------
# Evidence, hypotheses, verification
# ----------------------------------------------------------------------------

def _build_evidence(ctx: StreamContext, ep: Episode, w: dict, c: dict, peers: dict,
                    precedent: Optional[float], precedent_windows: int, tp: TriageParams) -> Tuple[List[dict], List[str]]:
    ev: List[dict] = []
    limits: List[str] = []
    n_open = w["n_open"]
    if ep.kind == "closure":
        ev.append(_evidence(
            f"The schedule lists the service as closed on {w['n_closed_unusual']} day(s) that are normally open "
            f"(typically ≈{_typical(w)} per day).", "schedule", supports=["NORMAL"], strength="strong"))
        if any("activity_on_closed_day" in a.flags for a in w["days"]):
            ev.append(_evidence("Activity was nevertheless recorded on a scheduled-closed day, so the schedule data may be outdated.",
                                "schedule", supports=["REPORTING"], contradicts=["NORMAL"]))
        return ev, limits

    # Activity on trusted days.
    if w["n_trusted"]:
        z = w["z_agg"]
        ev.append(_evidence(
            f"On {w['n_trusted']} open day(s) with trustworthy counts, {round(w['trusted_observed'])} were recorded against "
            f"≈{round(w['trusted_expected'])} expected ({_pct(w['trusted_frac'])} shortfall; aggregate z = {z:.1f}).",
            "activity",
            supports=["ACCESS"] if (w["trusted_frac"] or 0) >= tp.moderate_shortfall else ["NORMAL"],
            contradicts=["NORMAL", "REPORTING"] if _decline_strength(w, tp) == "STRONG" else [],
            strength="strong" if _decline_strength(w, tp) == "STRONG" else "moderate"))
    else:
        ev.append(_evidence("No day in this window has a trustworthy count, so the real activity level cannot be measured.",
                            "activity", supports=["REPORTING"], strength="strong"))
        limits.append("Real-world activity during this window is unknown: no trustworthy counts were received.")

    # Feed health.
    fd = w["flag_days"]
    if w["feed_status"] == "HEALTHY":
        ev.append(_evidence(
            f"Feed health looks normal on {n_open - w['n_missing'] - w['n_suspect']} of {n_open} open day(s): records arrived, "
            "heartbeats were received, upload batches were complete and values varied normally.",
            "feed", supports=["ACCESS"], contradicts=["REPORTING"], strength="strong"))
    else:
        parts = []
        if fd.get("missing_record"):
            parts.append(f"no record arrived on {fd['missing_record']} day(s)")
        if fd.get("missing_value"):
            parts.append(f"{fd['missing_value']} record(s) had no count")
        if fd.get("malformed"):
            parts.append(f"{fd['malformed']} count(s) were malformed")
        if fd.get("heartbeat_missing"):
            parts.append(f"the heartbeat was missing on {fd['heartbeat_missing']} day(s)")
        if fd.get("incomplete_batches"):
            parts.append(f"upload batches were incomplete on {fd['incomplete_batches']} day(s)")
        if fd.get("stale_feed"):
            parts.append(f"the upstream source had not been seen for over a day on {fd['stale_feed']} day(s)")
        ev.append(_evidence(
            f"Feed health is {w['feed_status'].lower()}: " + ("; ".join(parts) or "records are doubtful") +
            f" ({_pct(w['feed_frac'])} of open days affected).",
            "feed", supports=["REPORTING"], strength="strong" if w["feed_status"] == "FAILED" else "moderate"))
        if fd.get("zero_fill_suspected"):
            ev.append(_evidence(
                f"Zero was reported on {fd['zero_fill_suspected']} day(s) while the feed was down — a pattern typical of "
                "placeholder zeros, not of an observed absence of activity.", "feed", supports=["REPORTING"],
                contradicts=["ACCESS"], strength="strong"))
        if fd.get("flatline"):
            ev.append(_evidence(
                f"The same value was repeated on {fd['flatline']} consecutive open day(s); ordinary variation at this volume "
                "makes such a run very unlikely (estimated p < 0.0001).", "feed", supports=["REPORTING"],
                contradicts=["ACCESS", "NORMAL"], strength="strong"))
        limits.append("Counts on days with feed problems were excluded from the decline estimate.")

    # Schedule.
    if w["inferred_schedule_days"]:
        limits.append(f"The schedule for {w['inferred_schedule_days']} day(s) was inferred from the usual weekly pattern because no record arrived.")
    if w["n_closed_unusual"]:
        ev.append(_evidence(
            f"{w['n_closed_unusual']} normally-open day(s) inside the window were closed by schedule and excluded; "
            f"the remaining {n_open} open day(s) were assessed.", "schedule", supports=["NORMAL"], strength="weak"))
    else:
        ev.append(_evidence(
            f"The service was scheduled open on all {n_open} assessed day(s); no planned closure explains the gap.",
            "schedule", contradicts=["NORMAL"], strength="moderate"))

    # Context (coincidence only).
    ctx_items = []
    if c["severe_weather_days"]:
        ctx_items.append(f"severe weather (index up to {c['weather_max']}) on {c['severe_weather_days']} day(s)")
    if c["transport_days"]:
        ctx_items.append(f"a transport disruption on {c['transport_days']} day(s)")
    if c["outage_days"]:
        ctx_items.append(f"a reported operational outage on {c['outage_days']} day(s)")
    if ctx_items:
        supports = ["ACCESS"] + (["REPORTING"] if c["outage_days"] else [])
        ev.append(_evidence(
            "Context coinciding with the window: " + "; ".join(ctx_items) +
            ". This is a coincidence in time; it does not by itself establish a cause.", "context", supports=supports))
    else:
        ev.append(_evidence("No weather, transport or outage signal coincides with this window, so any cause is unobserved.",
                            "context", strength="weak"))

    # Peer comparison.
    if peers["compared"]:
        same = peers["declining"]
        if same == 0:
            ev.append(_evidence(
                f"None of {peers['compared']} comparable service(s) at the same site or region show a similar decline over these days, "
                "which points to a local rather than area-wide cause.", "peers", supports=["ACCESS", "REPORTING"], contradicts=[],
                strength="weak"))
        else:
            ev.append(_evidence(
                f"{same} of {peers['compared']} comparable service(s) at the same site or region also declined over these days, "
                "consistent with a shared, area-wide cause.", "peers", supports=["ACCESS"], strength="moderate"))

    # Historical precedent.
    if precedent is not None:
        if precedent >= tp.precedent_threshold:
            ev.append(_evidence(
                f"Ordinary variation produced a comparable shortfall in {_pct(precedent)} of {precedent_windows} earlier windows.",
                "history", supports=["NORMAL"], contradicts=["ACCESS"], strength="moderate"))
        else:
            ev.append(_evidence(
                f"A shortfall this large occurred in only {_pct(precedent)} of {precedent_windows} earlier windows of ordinary activity.",
                "history", supports=["ACCESS", "REPORTING"], contradicts=["NORMAL"], strength="moderate"))

    if w["history_median"] < tp.min_history:
        limits.append(f"Expected activity rests on only {int(w['history_median'])} comparable past day(s).")
    else:
        ev.append(_evidence(
            f"Expected activity is the median of {int(w['history_median'])} comparable past weekdays (same weekday, previous "
            "8 weeks, trusted days only).", "history", strength="weak"))
    if w["mean_expected"] is not None and w["mean_expected"] < tp.low_volume:
        limits.append(f"Low-volume service (≈{w['mean_expected']:.0f} per day): counts are noisy, so modest changes are hard to separate from chance.")
    return ev, limits


def _typical(w: dict) -> str:
    vals = [a.typical_if_open for a in w["days"] if a.status == "closed_unusual" and a.typical_if_open is not None]
    return str(round(stats.median(vals))) if vals else "unknown"


def _hypotheses(evidence: List[dict], category: str) -> List[dict]:
    out = []
    for key, label in HYPOTHESES.items():
        sup = [e["text"] for e in evidence if key in e["supports"]]
        con = [e["text"] for e in evidence if key in e["contradicts"]]
        strong_sup = sum(1 for e in evidence if key in e["supports"] and e["strength"] == "strong")
        strong_con = sum(1 for e in evidence if key in e["contradicts"] and e["strength"] == "strong")
        if not sup and not con:
            verdict = "Cannot assess"
        elif strong_con and not strong_sup:
            verdict = "Not supported"
        elif sup and not con:
            verdict = "Supported"
        elif len(sup) > len(con):
            verdict = "Partly supported"
        elif len(sup) == len(con):
            verdict = "Contested"
        else:
            verdict = "Not supported"
        out.append({"key": key, "label": label, "verdict": verdict, "supporting": sup, "contradicting": con})
    return out


def _verification_steps(category: str, ctx: StreamContext, ep: Episode, w: dict, c: dict) -> List[str]:
    span = f"{_fmt_date(ep.onset)} – {_fmt_date(ep.end)}"
    site = ctx.site_name
    steps: List[str] = []
    if category in (ACCESS, UNCERTAIN):
        steps.append(f"Contact the {site} service lead to confirm whether {ctx.service_type.replace('_', ' ')} operated normally during {span}.")
        if c["transport_days"]:
            steps.append("Check with the transport operator whether the reported disruption affected routes serving this location.")
        if c["severe_weather_days"]:
            steps.append("Confirm local conditions (access roads, closures) on the severe-weather days with the site or emergency management.")
        if c["outage_days"]:
            steps.append("Confirm the outage window and whether a manual fallback process (paper forms, walk-in) was available.")
        steps.append("Compare against an independent aggregate source (bookings, queue logs, stock movements) before drawing conclusions.")
    if category in (REPORTING, UNCERTAIN):
        last_seen = None
        for a in reversed(w["days"]):
            if a.stale_days is not None and a.stale_days <= 1.0:
                last_seen = a.date
                break
        steps.append("Ask the data-pipeline owner to check the feed: upload logs, heartbeat service and source-system connectivity"
                     + (f" (last healthy report around {_fmt_date(last_seen)})." if last_seen else "."))
        steps.append("Request a manual or back-filled count for the affected days from the site.")
        steps.append("Re-run the analysis after the feed is restored to see whether a real decline remains.")
    if category == EXPECTED:
        steps.append("No investigation required. Optionally confirm that the operating schedule data is current.")
    if category != EXPECTED:
        steps.append("Record the outcome in the review panel so the decision is auditable. Do not treat this alert as evidence about any individual.")
    return steps


# ----------------------------------------------------------------------------
# Scores (documented heuristics, not probabilities)
# ----------------------------------------------------------------------------

def _priority(category: str, ep: Episode, w: dict, as_of: date) -> Tuple[int, dict]:
    unaccounted = w["shortfall_units"] + w["missing_expected"]
    magnitude = stats.clamp((unaccounted / w["expected_total"]) / 0.8) if w["expected_total"] > 0 else 0.0
    persistence = stats.clamp(ep.nonok_days / 7.0)
    days_since = max(0, (as_of - ep.end).days)
    recency = 1.0 if ep.ongoing else math.exp(-days_since / 14.0)
    weight = CATEGORY_WEIGHT[category]
    score = round(100 * weight * (0.5 * magnitude + 0.3 * persistence + 0.2 * recency))
    return score, {"category_weight": weight, "magnitude": round(magnitude, 3), "persistence": round(persistence, 3),
                   "recency": round(recency, 3),
                   "formula": "100 × category_weight × (0.5·magnitude + 0.3·persistence + 0.2·recency)"}


def _evidence_score(category: str, ep: Episode, w: dict, tp: TriageParams) -> Tuple[int, dict]:
    history = stats.clamp(w["history_median"] / 8.0)
    feed_clarity = {"HEALTHY": 1.0, "FAILED": 0.9, "DEGRADED": 0.35}[w["feed_status"]]
    n_open = max(1, w["n_open"])
    if ep.kind == "closure":
        signal, consistency, history, feed_clarity = 1.0, 1.0, max(history, 0.5), 1.0
    elif category == REPORTING:
        explicit = sum(1 for a in w["open_days"] if a.status in ("missing", "suspect"))
        signal = stats.clamp(explicit / n_open)
        consistency = signal
    elif category in (ACCESS, UNCERTAIN) and w["z_agg"] is not None:
        signal = stats.clamp(abs(min(0.0, w["z_agg"])) / 8.0)
        consistency = stats.clamp(w["n_low"] / n_open)
    else:
        signal = 0.3
        consistency = stats.clamp(1 - w["feed_frac"])
    persistence = stats.clamp(max(ep.nonok_days, w["n_closed_unusual"]) / 5.0)
    score = round(100 * (0.25 * history + 0.25 * signal + 0.2 * feed_clarity + 0.15 * consistency + 0.15 * persistence))
    return score, {"history": round(history, 3), "signal": round(signal, 3), "feed_clarity": feed_clarity,
                   "consistency": round(consistency, 3), "persistence": round(persistence, 3),
                   "formula": "100 × (0.25·history + 0.25·signal + 0.2·feed_clarity + 0.15·consistency + 0.15·persistence)"}


def band(score: int, kind: str) -> str:
    if kind == "priority":
        return "High" if score >= 70 else "Medium" if score >= 45 else "Low"
    return "Strong" if score >= 70 else "Moderate" if score >= 45 else "Limited"


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def triage_episode(ctx: StreamContext, ep: Episode, peers: Sequence[StreamContext], as_of: date,
                   tp: TriageParams = TriageParams(), simple: bool = False) -> dict:
    """Build a full case record for one episode.

    ``simple=True`` is the ablation used in evaluation: every non-closure
    episode is reported as a potential access gap, with no feed or context
    reasoning.
    """
    w = _window_features(ctx, ep, tp)
    c = _context_features(ctx, ep)
    if simple:
        category = EXPECTED if ep.kind == "closure" else ACCESS
        reason = "scheduled_closure" if ep.kind == "closure" else "decline_detected"
        precedent, windows, pf = None, 0, {"compared": 0, "declining": 0, "peers": []}
    else:
        pf = _peer_features(ctx, ep, peers, tp)
        precedent, windows = _precedent_rate(ctx, ep, w["trusted_frac"], w["n_trusted"])
        category, reason = _decide(ep, w, c, precedent, tp)
    priority, pcomp = _priority(category, ep, w, as_of)
    escore, ecomp = _evidence_score(category, ep, w, tp)
    case = {
        "case_id": case_id_for(ctx.site_id, ctx.service_type, ep.onset),
        "stream_id": ctx.stream_id, "site_id": ctx.site_id, "site_name": ctx.site_name, "region": ctx.region,
        "service_type": ctx.service_type, "kind": ep.kind,
        "category": category, "category_label": CATEGORY_LABELS[category], "reason_code": reason,
        "actionable": category in ACTIONABLE,
        "onset": ep.onset.isoformat(), "start": ep.start.isoformat(), "end": ep.end.isoformat(),
        "alert_date": ep.alert_date.isoformat() if ep.alert_date else None, "ongoing": ep.ongoing,
        "duration_days": (ep.end - ep.onset).days + 1, "open_days": w["n_open"], "nonok_days": ep.nonok_days,
        "expected_total": round(w["expected_total"], 1), "reported_total": int(w["reported_total"]),
        "trusted_expected": round(w["trusted_expected"], 1), "trusted_observed": int(w["trusted_observed"]),
        "shortfall_units": round(w["shortfall_units"], 1), "missing_expected": round(w["missing_expected"], 1),
        "unaccounted_total": round(w["shortfall_units"] + w["missing_expected"], 1),
        "shortfall_frac": stats.round_or_none(w["trusted_frac"]), "z_agg": stats.round_or_none(w["z_agg"], 2),
        "min_z": stats.round_or_none(w["min_z"], 2), "feed_status": w["feed_status"],
        "feed_problem_frac": round(w["feed_frac"], 3), "flag_days": w["flag_days"],
        "status_counts": w["status_counts"], "history_median": w["history_median"],
        "mean_expected": stats.round_or_none(w["mean_expected"], 1),
        "context": c, "peers": pf, "precedent_rate": stats.round_or_none(precedent), "precedent_windows": windows,
        "priority": priority, "priority_band": band(priority, "priority"), "priority_components": pcomp,
        "evidence_score": escore, "evidence_band": band(escore, "evidence"), "evidence_components": ecomp,
    }
    if simple:
        return case
    evidence, limits = _build_evidence(ctx, ep, w, c, pf, precedent, windows, tp)
    limits = limits + [
        "This is a hypothesis for human verification, not a finding. A gap in recorded activity does not show that any person was denied a service.",
        "Context signals are shown because they coincide in time; the engine does not estimate causal effects.",
    ]
    if category == ACCESS:
        limits.append("A reporting fault that leaves feed metadata intact (for example, an export that silently drops a category) would look identical; confirm with the site.")
    case.update({
        "headline": _headline(category, ctx, ep, w),
        "explanation": REASON_TEXT.get(reason, ""),
        "evidence": evidence,
        "supporting": [e for e in evidence if _maps(category) in e["supports"]],
        "conflicting": [e for e in evidence if _maps(category) in e["contradicts"]],
        "hypotheses": _hypotheses(evidence, category),
        "limitations": limits,
        "verification_steps": _verification_steps(category, ctx, ep, w, c),
    })
    return case


def _maps(category: str) -> str:
    return {ACCESS: "ACCESS", REPORTING: "REPORTING", EXPECTED: "NORMAL", UNCERTAIN: "ACCESS"}[category]


def _headline(category: str, ctx: StreamContext, ep: Episode, w: dict) -> str:
    svc = ctx.service_type.replace("_", " ")
    if category == ACCESS:
        return f"{svc.capitalize()} at {ctx.site_name} is {_pct(w['trusted_frac'])} below expected while its data feed looks healthy."
    if category == REPORTING:
        return f"{svc.capitalize()} at {ctx.site_name} went quiet, but the feed shows {w['feed_status'].lower()} reporting — likely a data problem."
    if category == UNCERTAIN:
        return f"{svc.capitalize()} at {ctx.site_name} declined, but the evidence is mixed — needs investigation."
    if ep.kind == "closure":
        return f"{svc.capitalize()} at {ctx.site_name} was closed by schedule on normally-open days."
    return f"{svc.capitalize()} at {ctx.site_name} dipped within its normal range."
