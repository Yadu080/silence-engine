"""Read-side aggregates for the API, computed from the persisted analysis run.

Every number shown in the dashboard comes from these functions, which read the
same ``daily_assessments`` and ``cases`` tables, so charts, counts, queues and
the silence map always agree with each other.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import database
from .triage import ACCESS, ACTIONABLE, CATEGORY_LABELS, EXPECTED, REPORTING, UNCERTAIN

STATUS_CODES = {"closed": 0, "closed_unusual": 1, "ok": 2, "low": 3, "missing": 4, "suspect": 5, "no_baseline": 6}
OPEN_REVIEW_STATES = (None, "under_review")
REVIEW_STATUSES = ("under_review", "verified_access_gap", "verified_reporting_issue", "verified_expected", "dismissed")

_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[str, List[dict]]] = {}


def _rows(conn: sqlite3.Connection, run_id: str, key: str) -> List[dict]:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == run_id:
            return hit[1]
    rows = []
    for r in database.load_day_rows(conn):
        d = dict(r)
        d["flags"] = json.loads(d["flags"]) if d["flags"] else []
        rows.append(d)
    with _cache_lock:
        _cache[key] = (run_id, rows)
    return rows


def day_rows(conn: sqlite3.Connection, run: dict, db_key: str) -> List[dict]:
    return _rows(conn, run["run_id"], db_key)


def _cases_by_id(conn: sqlite3.Connection) -> Dict[str, dict]:
    return {c["case_id"]: c for c in database.load_cases(conn)}


def _gap(row: dict) -> Tuple[float, bool]:
    """(units of expected activity not seen, unknown?) for an open day."""
    exp = row["expected"]
    if not row["scheduled_open"] or exp is None:
        return 0.0, False
    if row["status"] in ("missing", "suspect"):
        return float(exp), True
    if row["observed"] is None:
        return 0.0, False
    return max(0.0, exp - row["observed"]), False


# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

def summary(conn: sqlite3.Connection, run: dict, db_key: str, window: int = 28) -> dict:
    rows = day_rows(conn, run, db_key)
    as_of = date.fromisoformat(run["end_date"])
    lo = as_of - timedelta(days=window - 1)
    cases = database.load_cases(conn)
    reviews = database.latest_review_status(conn)
    case_cat = {c["case_id"]: c["category"] for c in cases if c["kind"] == "silence"}

    ledger = {ACCESS: 0.0, REPORTING: 0.0, UNCERTAIN: 0.0, EXPECTED: 0.0}
    outside = 0.0
    unknown_total = 0.0
    expected_total = 0.0
    trusted_obs = 0.0
    trusted_exp = 0.0
    for r in rows:
        d = date.fromisoformat(r["date"])
        if d < lo or not r["scheduled_open"] or r["expected"] is None:
            continue
        expected_total += r["expected"]
        gap, unknown = _gap(r)
        if unknown:
            unknown_total += gap
        elif r["status"] in ("ok", "low") and r["observed"] is not None:
            trusted_obs += r["observed"]
            trusted_exp += r["expected"]
        cat = case_cat.get(r["case_id"]) if r["case_id"] else None
        if cat:
            ledger[cat] += gap
        else:
            outside += gap

    recent = [c for c in cases if c["end"] >= lo.isoformat()]
    recent_actionable = [c for c in recent if c["actionable"]]
    attention = [c for c in recent_actionable if (reviews.get(c["case_id"]) or {}).get("status") in OPEN_REVIEW_STATES]
    dq = database.current_dq(conn) or {"streams": []}
    feeds_bad = [s for s in dq["streams"] if s["status"] != "HEALTHY"]

    def ratio(lo_d: date, hi_d: date) -> Optional[float]:
        o = e = 0.0
        for r in rows:
            d = date.fromisoformat(r["date"])
            if lo_d <= d <= hi_d and r["status"] in ("ok", "low") and r["expected"] and r["observed"] is not None:
                o += r["observed"]
                e += r["expected"]
        return round(o / e - 1, 4) if e else None

    last7 = ratio(as_of - timedelta(days=6), as_of)
    prev7 = ratio(as_of - timedelta(days=13), as_of - timedelta(days=7))

    movers = []
    streams = sorted({r["stream_id"] for r in rows})
    for sid in streams:
        o = e = 0.0
        unknown_days = 0
        for r in rows:
            if r["stream_id"] != sid:
                continue
            d = date.fromisoformat(r["date"])
            if as_of - timedelta(days=6) <= d <= as_of and r["scheduled_open"]:
                if r["status"] in ("missing", "suspect"):
                    unknown_days += 1
                elif r["status"] in ("ok", "low") and r["expected"] and r["observed"] is not None:
                    o += r["observed"]
                    e += r["expected"]
        movers.append({"stream_id": sid, "change": round(o / e - 1, 4) if e else None, "unknown_days": unknown_days})
    movers.sort(key=lambda m: (m["change"] is None, m["change"] if m["change"] is not None else 0))

    in_cases = sum(ledger[c] for c in (ACCESS, REPORTING, UNCERTAIN))
    sites = {r["site_id"] for r in rows}
    return {
        "as_of": as_of.isoformat(), "window_days": window, "window_start": lo.isoformat(),
        "sites_monitored": len(sites), "streams_monitored": len(streams),
        "investigations_requiring_attention": len(attention),
        "recent_actionable_cases": len(recent_actionable),
        "recent_by_category": {cat: sum(1 for c in recent if c["category"] == cat) for cat in CATEGORY_LABELS},
        "all_time_by_category": {cat: sum(1 for c in cases if c["category"] == cat) for cat in CATEGORY_LABELS},
        "potential_access_gaps": sum(1 for c in recent_actionable if c["category"] == ACCESS),
        "reporting_problems": sum(1 for c in recent_actionable if c["category"] == REPORTING),
        "uncertain": sum(1 for c in recent_actionable if c["category"] == UNCERTAIN),
        "feeds_not_healthy": len(feeds_bad),
        "feeds_not_healthy_list": [{"stream_id": s["stream_id"], "status": s["status"]} for s in feeds_bad],
        "activity_change_last7": last7, "activity_change_prev7": prev7,
        "movers": movers[:5],
        "ledger": {
            "expected_total": round(expected_total), "trusted_observed": round(trusted_obs),
            "trusted_expected": round(trusted_exp), "unknown_due_to_feed": round(unknown_total),
            "unaccounted_in_cases": round(in_cases),
            "by_category": {k: round(v) for k, v in ledger.items()},
            "outside_cases_shortfall": round(outside),
        },
        "top_cases": [_case_brief(c, reviews) for c in recent_actionable[:6]],
        "run": {"run_id": run["run_id"], "finished_at": run["finished_at"], "params": run["params"]},
    }


def _case_brief(c: dict, reviews: Dict[str, dict]) -> dict:
    rv = reviews.get(c["case_id"])
    keys = ("case_id", "stream_id", "site_id", "site_name", "region", "service_type", "kind", "category",
            "category_label", "reason_code", "actionable", "onset", "end", "alert_date", "ongoing", "duration_days",
            "open_days", "expected_total", "reported_total", "trusted_expected", "trusted_observed", "shortfall_frac",
            "unaccounted_total", "missing_expected", "feed_status", "priority", "priority_band", "evidence_score",
            "evidence_band", "headline", "z_agg")
    out = {k: c.get(k) for k in keys}
    ctx = c.get("context") or {}
    out["context_flags"] = {
        "severe_weather_days": ctx.get("severe_weather_days", 0), "weather_max": ctx.get("weather_max"),
        "transport_days": ctx.get("transport_days", 0), "outage_days": ctx.get("outage_days", 0),
    }
    out["review_status"] = rv["status"] if rv else None
    return out


# ----------------------------------------------------------------------------
# Cases
# ----------------------------------------------------------------------------

SORT_KEYS = {
    "priority": lambda c: c["priority"],
    "onset": lambda c: c["onset"],
    "shortfall": lambda c: c.get("unaccounted_total") or 0,
    "evidence": lambda c: c["evidence_score"],
    "duration": lambda c: c["duration_days"],
}


def list_cases(conn: sqlite3.Connection, categories: Optional[List[str]] = None, include_expected: bool = False,
               site_id: Optional[str] = None, feed_status: Optional[str] = None, ongoing: Optional[bool] = None,
               since: Optional[str] = None, review: Optional[str] = None, sort: str = "priority",
               descending: bool = True, limit: int = 100, offset: int = 0) -> dict:
    cases = database.load_cases(conn)
    reviews = database.latest_review_status(conn)
    totals = {cat: sum(1 for c in cases if c["category"] == cat) for cat in CATEGORY_LABELS}
    items = []
    for c in cases:
        if categories and c["category"] not in categories:
            continue
        if not categories and not include_expected and c["category"] == EXPECTED:
            continue
        if site_id and c["site_id"] != site_id:
            continue
        if feed_status and c["feed_status"] != feed_status:
            continue
        if ongoing is not None and bool(c["ongoing"]) != ongoing:
            continue
        if since and c["end"] < since:
            continue
        status = (reviews.get(c["case_id"]) or {}).get("status")
        if review == "open" and status not in OPEN_REVIEW_STATES:
            continue
        if review == "reviewed" and status in OPEN_REVIEW_STATES:
            continue
        items.append(c)
    key = SORT_KEYS.get(sort, SORT_KEYS["priority"])
    items.sort(key=lambda c: (key(c), c["onset"]), reverse=descending)
    page = items[offset: offset + limit]
    return {"total": len(items), "offset": offset, "limit": limit, "category_totals": totals,
            "items": [_case_brief(c, reviews) for c in page]}


def case_detail(conn: sqlite3.Connection, run: dict, db_key: str, case_id: str) -> Optional[dict]:
    case = database.load_case(conn, case_id)
    if not case:
        return None
    rows = [r for r in day_rows(conn, run, db_key) if r["stream_id"] == case["stream_id"]]
    lo = (date.fromisoformat(case["onset"]) - timedelta(days=35)).isoformat()
    hi = (date.fromisoformat(case["end"]) + timedelta(days=14)).isoformat()
    timeline = [_day_view(r) for r in rows if lo <= r["date"] <= hi]
    related = [_case_brief(c, {}) for c in database.load_cases(conn)
               if c["stream_id"] == case["stream_id"] and c["case_id"] != case_id]
    related.sort(key=lambda c: c["onset"], reverse=True)
    from . import audit  # local import keeps module import graph simple
    return {
        "case": case,
        "timeline": timeline,
        "reviews": database.reviews_for(conn, case_id),
        "audit": audit.list_entries(conn, limit=20, entity_id=case_id),
        "related": related[:8],
        "review_statuses": list(REVIEW_STATUSES),
    }


def _day_view(r: dict) -> dict:
    exp = r["expected"]
    sigma = r["sigma"]
    return {
        "date": r["date"], "observed": r["observed"], "expected": None if exp is None else round(exp, 2),
        "band_low": None if exp is None or sigma is None else round(max(0.0, exp - 2 * sigma), 2),
        "band_high": None if exp is None or sigma is None else round(exp + 2 * sigma, 2),
        "typical_if_open": r["typical_if_open"], "status": r["status"], "value_status": r["value_status"],
        "scheduled_open": bool(r["scheduled_open"]), "schedule_inferred": bool(r["schedule_inferred"]),
        "trusted": bool(r["trusted"]), "z": None if r["z"] is None else round(r["z"], 2),
        "shortfall_frac": None if r["shortfall_frac"] is None else round(r["shortfall_frac"], 3),
        "n_history": r["n_history"], "flags": r["flags"], "weather": r["weather"], "transport": bool(r["transport"]),
        "outage": bool(r["outage"]), "heartbeat": None if r["heartbeat"] is None else bool(r["heartbeat"]),
        "batch_completeness": r["batch_completeness"], "stale_days": r["stale_days"], "case_id": r["case_id"],
        "cusum": r["cusum"],
    }


# ----------------------------------------------------------------------------
# Time series and silence map
# ----------------------------------------------------------------------------

def streams(conn: sqlite3.Connection, run: dict, db_key: str) -> List[dict]:
    rows = day_rows(conn, run, db_key)
    sites = database.load_sites(conn)
    out: Dict[str, dict] = {}
    for r in rows:
        s = out.get(r["stream_id"])
        if s is None:
            meta = sites.get(r["site_id"], {})
            s = out[r["stream_id"]] = {
                "stream_id": r["stream_id"], "site_id": r["site_id"], "service_type": r["service_type"],
                "site_name": meta.get("site_name") or r["site_id"], "region": meta.get("region"),
                "synthetic": bool(meta.get("synthetic")), "first_date": r["date"], "last_date": r["date"],
                "_vals": [],
            }
        s["last_date"] = r["date"]
        if r["status"] in ("ok", "low") and r["observed"] is not None:
            s["_vals"].append(r["observed"])
    result = []
    for s in out.values():
        vals = sorted(s.pop("_vals"))
        s["typical_daily"] = vals[len(vals) // 2] if vals else None
        result.append(s)
    return sorted(result, key=lambda s: (s["site_name"], s["service_type"]))


def timeseries(conn: sqlite3.Connection, run: dict, db_key: str, stream_id: str,
               start: Optional[str] = None, end: Optional[str] = None) -> Optional[dict]:
    rows = day_rows(conn, run, db_key)
    if stream_id != "ALL":
        sel = [r for r in rows if r["stream_id"] == stream_id]
        if not sel:
            return None
        sel = [r for r in sel if (not start or r["date"] >= start) and (not end or r["date"] <= end)]
        return {"stream_id": stream_id, "aggregate": False, "days": [_day_view(r) for r in sel]}
    by_date: Dict[str, dict] = {}
    for r in rows:
        if (start and r["date"] < start) or (end and r["date"] > end):
            continue
        agg = by_date.setdefault(r["date"], {"date": r["date"], "expected": 0.0, "observed": 0.0, "unknown": 0.0,
                                             "streams_reporting": 0, "streams_unknown": 0, "case_streams": 0})
        if not r["scheduled_open"] or r["expected"] is None:
            continue
        agg["expected"] += r["expected"]
        if r["status"] in ("missing", "suspect"):
            agg["unknown"] += r["expected"]
            agg["streams_unknown"] += 1
        elif r["observed"] is not None:
            agg["observed"] += r["observed"]
            agg["streams_reporting"] += 1
        if r["case_id"]:
            agg["case_streams"] += 1
    days = []
    for d in sorted(by_date):
        a = by_date[d]
        days.append({k: (round(v, 1) if isinstance(v, float) else v) for k, v in a.items()})
    return {"stream_id": "ALL", "aggregate": True, "days": days}


def silence_map(conn: sqlite3.Connection, run: dict, db_key: str, start: Optional[str] = None,
                end: Optional[str] = None) -> dict:
    rows = day_rows(conn, run, db_key)
    rows = [r for r in rows if (not start or r["date"] >= start) and (not end or r["date"] <= end)]
    dates = sorted({r["date"] for r in rows})
    pos = {d: i for i, d in enumerate(dates)}
    cases = {c["case_id"]: c for c in database.load_cases(conn)}
    case_index: Dict[str, int] = {}
    case_list: List[dict] = []
    stream_rows: Dict[str, dict] = {}
    meta = {s["stream_id"]: s for s in streams(conn, run, db_key)}
    for r in rows:
        sr = stream_rows.get(r["stream_id"])
        if sr is None:
            m = meta.get(r["stream_id"], {})
            sr = stream_rows[r["stream_id"]] = {
                "stream_id": r["stream_id"], "site_name": m.get("site_name", r["site_id"]),
                "service_type": r["service_type"], "region": m.get("region"),
                "cells": [None] * len(dates),
            }
        gap = None
        if r["status"] in ("missing", "suspect"):
            gap = 1.0
        elif r["status"] in ("ok", "low") and r["shortfall_frac"] is not None:
            gap = round(max(0.0, min(1.0, r["shortfall_frac"])), 2)
        ci = -1
        cid = r["case_id"]
        if cid and cid in cases:
            if cid not in case_index:
                case_index[cid] = len(case_list)
                c = cases[cid]
                case_list.append({"case_id": cid, "category": c["category"], "onset": c["onset"], "end": c["end"]})
            ci = case_index[cid]
        sr["cells"][pos[r["date"]]] = [STATUS_CODES[r["status"]], gap, ci]
    return {"dates": dates, "status_codes": STATUS_CODES, "streams": list(stream_rows.values()), "cases": case_list}


# ----------------------------------------------------------------------------
# Data quality
# ----------------------------------------------------------------------------

def data_quality(conn: sqlite3.Connection, run: dict, db_key: str, days: int = 90) -> dict:
    dq = database.current_dq(conn) or {"streams": [], "issues": [], "issue_day_totals": {}}
    rows = day_rows(conn, run, db_key)
    as_of = date.fromisoformat(run["end_date"])
    lo = (as_of - timedelta(days=days - 1)).isoformat()
    dates = sorted({r["date"] for r in rows if r["date"] >= lo})
    pos = {d: i for i, d in enumerate(dates)}
    matrix: Dict[str, List[Optional[str]]] = {}
    for r in rows:
        if r["date"] < lo:
            continue
        cells = matrix.setdefault(r["stream_id"], [None] * len(dates))
        if not r["scheduled_open"]:
            code = "closed"
        elif r["status"] == "missing":
            code = "missing"
        elif r["status"] == "suspect":
            code = "suspect"
        elif any(f in r["flags"] for f in ("duplicate", "duplicate_conflict", "late_arrival", "malformed")):
            code = "warning"
        else:
            code = "ok"
        cells[pos[r["date"]]] = code
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    issues = sorted(dq.get("issues", []), key=lambda i: (i["end"], -severity_rank.get(i["severity"], 3)), reverse=True)
    return {
        "as_of": dq.get("as_of"), "streams": dq.get("streams", []), "issues": issues,
        "issue_day_totals": dq.get("issue_day_totals", {}),
        "feed_matrix": {"dates": dates, "streams": [{"stream_id": k, "cells": v} for k, v in sorted(matrix.items())]},
        "note": "Data-quality findings describe the reporting pipeline, not service delivery. They are kept separate "
                "from service-gap alerts on purpose.",
    }
