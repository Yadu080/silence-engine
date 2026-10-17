"""Analysis pipeline: observations -> feed health -> baselines -> episodes -> cases.

Pure functions over ``Observation`` objects. No database, web or label access,
so the same pipeline is used by the API, the CLI and the evaluation harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence

from . import data_quality, detection, triage
from .data_quality import StreamData
from .detection import Baseline, DayAssessment, DetectionParams, Episode
from .ingest import Observation
from .triage import StreamContext, TriageParams


@dataclass
class Prepared:
    streams: Dict[str, StreamData]
    baselines: Dict[str, List[Baseline]]
    end_date: Optional[date]
    sites: Dict[str, dict]
    use_feed_health: bool
    lookback_weeks: int
    min_history: int


@dataclass
class AnalysisResult:
    end_date: Optional[date]
    params: DetectionParams
    triage_params: TriageParams
    streams: Dict[str, StreamData]
    assessments: Dict[str, List[DayAssessment]]
    contexts: Dict[str, StreamContext]
    episodes: Dict[str, List[Episode]]
    cases: List[dict] = field(default_factory=list)

    def actionable_cases(self) -> List[dict]:
        return [c for c in self.cases if c["actionable"]]


def prepare(observations: Iterable[Observation], sites: Optional[Dict[str, dict]] = None,
            end_date: Optional[date] = None, visible_at: Optional[datetime] = None,
            params: DetectionParams = DetectionParams()) -> Prepared:
    streams = data_quality.prepare_streams(observations, end_date=end_date, visible_at=visible_at)
    baselines = {
        sid: detection.compute_baselines(sd.days, params.lookback_weeks, params.min_history, params.use_feed_health)
        for sid, sd in streams.items()
    }
    if end_date is None and streams:
        end_date = max(sd.last_date for sd in streams.values())
    return Prepared(streams, baselines, end_date, dict(sites or {}), params.use_feed_health,
                    params.lookback_weeks, params.min_history)


def analyze_prepared(prep: Prepared, params: DetectionParams = DetectionParams(),
                     tp: TriageParams = TriageParams(), simple_triage: bool = False,
                     with_cases: bool = True) -> AnalysisResult:
    if params.use_feed_health != prep.use_feed_health:
        raise ValueError("Prepared data was built with a different use_feed_health setting")
    assessments: Dict[str, List[DayAssessment]] = {}
    contexts: Dict[str, StreamContext] = {}
    episodes: Dict[str, List[Episode]] = {}
    for sid, sd in prep.streams.items():
        days = detection.assess_stream(sd, prep.baselines[sid], params)
        assessments[sid] = days
        meta = prep.sites.get(sd.site_id, {})
        contexts[sid] = triage.make_context(sid, sd.site_id, meta.get("site_name") or sd.site_id,
                                            meta.get("region"), sd.service_type, days)
        episodes[sid] = detection.find_episodes(sid, days, params, as_of=prep.end_date)
    result = AnalysisResult(prep.end_date, params, tp, prep.streams, assessments, contexts, episodes)
    if with_cases and prep.end_date is not None:
        peer_list = list(contexts.values())
        for sid, eps in episodes.items():
            for ep in eps:
                result.cases.append(triage.triage_episode(contexts[sid], ep, peer_list, prep.end_date, tp,
                                                          simple=simple_triage))
        result.cases.sort(key=lambda c: (-int(c["actionable"]), -c["priority"], c["onset"]))
        _dedupe_case_ids(result.cases)
    return result


def analyze(observations: Sequence[Observation], sites: Optional[Dict[str, dict]] = None,
            params: DetectionParams = DetectionParams(), tp: TriageParams = TriageParams(),
            end_date: Optional[date] = None) -> AnalysisResult:
    prep = prepare(observations, sites, end_date=end_date, params=params)
    return analyze_prepared(prep, params, tp)


def _dedupe_case_ids(cases: List[dict]) -> None:
    seen: Dict[str, int] = {}
    for c in cases:
        base = c["case_id"]
        if base in seen:
            seen[base] += 1
            c["case_id"] = f"{base}-{seen[base]}"
        else:
            seen[base] = 1


def case_day_index(result: AnalysisResult) -> Dict[str, Dict[date, str]]:
    """Map stream -> date -> case_id for days inside a case window."""
    index: Dict[str, Dict[date, str]] = {}
    by_key = {(c["stream_id"], c["onset"], c["kind"]): c["case_id"] for c in result.cases}
    for sid, eps in result.episodes.items():
        days = result.assessments[sid]
        m = index.setdefault(sid, {})
        for ep in eps:
            cid = by_key.get((sid, ep.onset.isoformat(), ep.kind))
            if not cid:
                continue
            for i in range(ep.start_index, ep.end_index + 1):
                d = days[i].date
                # silence cases take precedence over closure notes on the same day
                if d not in m or ep.kind == "silence":
                    m[d] = cid
    return index


def dq_report(result: AnalysisResult) -> dict:
    as_of = result.end_date
    streams = []
    if as_of is not None:
        for sid, sd in result.streams.items():
            h = data_quality.stream_health(sd, as_of)
            ctx = result.contexts.get(sid)
            h["site_name"] = ctx.site_name if ctx else sd.site_id
            h["region"] = ctx.region if ctx else None
            streams.append(h)
    issues = data_quality.collect_issues(result.streams)
    totals: Dict[str, int] = {}
    for issue in issues:
        totals[issue["type"]] = totals.get(issue["type"], 0) + issue["days"]
    return {"as_of": as_of.isoformat() if as_of else None, "streams": streams, "issues": issues,
            "issue_day_totals": totals}
