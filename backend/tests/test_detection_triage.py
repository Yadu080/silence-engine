from datetime import date, timedelta

import pytest

from silence_engine import data_quality, detection, pipeline
from silence_engine.detection import DetectionParams
from silence_engine.triage import ACCESS, EXPECTED, REPORTING, UNCERTAIN
from tests.conftest import make_obs, steady_stream

S = "SITE:clinic"
START = date(2026, 1, 5)  # Monday
SITES = {"SITE": {"site_name": "Test Site", "region": "North"}}


def _v(base, i):
    """Vary injected values: identical repeats would (correctly) be flagged as a flatline."""
    return base + (-2, 1, 3, -1, 2, 0, -3, 1, 2, -2)[i % 10]


def _replace(obs, new):
    dates = {o.service_date for o in new}
    return [o for o in obs if o.service_date not in dates] + new


def _weekdays(start: date, n: int):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _cases(obs, **kw):
    res = pipeline.analyze(obs, SITES, DetectionParams(**kw))
    return [c for c in res.cases if c["kind"] == "silence"], res


def test_baseline_is_median_of_previous_same_weekdays():
    obs = steady_stream(S, START, 70)
    sd = data_quality.prepare_streams(obs)[S]
    base = detection.compute_baselines(sd.days)
    i = 63  # Monday, week 10
    prev = [sd.days[i - 7 * w].observed for w in range(1, 9)]
    assert base[i].expected == pytest.approx(sorted(prev)[3] / 2 + sorted(prev)[4] / 2)
    assert base[i].n_history == 8


def test_insufficient_history_yields_no_baseline():
    obs = steady_stream(S, START, 21)
    sd = data_quality.prepare_streams(obs)[S]
    days = detection.assess_stream(sd, detection.compute_baselines(sd.days), DetectionParams())
    assert all(a.status in ("no_baseline", "closed") for a in days)


def test_no_future_leakage_in_expected_values():
    """Changing data after day T must not change any expected value or status up to T."""
    obs = steady_stream(S, START, 84)
    cutoff = START + timedelta(days=60)
    altered = [o if o.service_date <= cutoff else make_obs(S, o.service_date, 3, open_=o.scheduled_open)
               for o in obs]
    a = pipeline.analyze(obs, SITES).assessments[S]
    b = pipeline.analyze(altered, SITES).assessments[S]
    for x, y in zip(a, b):
        if x.date <= cutoff:
            assert (x.expected, x.sigma, x.status, x.z) == (y.expected, y.sigma, y.status, y.z)


def test_genuine_decline_with_healthy_feed_is_potential_access_gap():
    obs = steady_stream(S, START, 84)
    gap = _weekdays(START + timedelta(days=70), 5)
    obs = _replace(obs, [make_obs(S, d, _v(35, i)) for i, d in enumerate(gap)])
    cases, _ = _cases(obs)
    assert len(cases) == 1
    c = cases[0]
    assert c["category"] == ACCESS and c["feed_status"] == "HEALTHY"
    assert c["onset"] == gap[0].isoformat()
    assert c["alert_date"] == gap[1].isoformat()  # min_persistence = 2
    assert c["shortfall_frac"] > 0.5
    assert c["evidence"] and c["verification_steps"] and c["limitations"]
    assert {h["key"] for h in c["hypotheses"]} == {"ACCESS", "REPORTING", "NORMAL"}
    assert 0 <= c["priority"] <= 100 and 0 <= c["evidence_score"] <= 100


def test_dropout_is_reporting_problem_not_access_gap():
    obs = steady_stream(S, START, 84)
    gap = set(_weekdays(START + timedelta(days=70), 4))
    obs = [o for o in obs if o.service_date not in gap]
    cases, _ = _cases(obs)
    assert [c["category"] for c in cases] == [REPORTING]
    assert cases[0]["flag_days"].get("missing_record") == 4


def test_zero_fill_feed_failure_vs_recorded_zero_decline():
    """Same zeros in the data; the feed evidence decides the hypothesis."""
    base = steady_stream(S, START, 84)
    gap = _weekdays(START + timedelta(days=70), 4)
    feed_down = _replace(base, [make_obs(S, d, 0, heartbeat=False, received=0) for d in gap])
    feed_ok = _replace(base, [make_obs(S, d, 0, heartbeat=True) for d in gap])
    down_cases, _ = _cases(feed_down)
    ok_cases, _ = _cases(feed_ok)
    assert [c["category"] for c in down_cases] == [REPORTING]
    assert [c["category"] for c in ok_cases] == [ACCESS]


def test_decline_with_degraded_feed_is_uncertain():
    obs = steady_stream(S, START, 84)
    gap = _weekdays(START + timedelta(days=70), 6)
    new = []
    for i, d in enumerate(gap):
        new.append(make_obs(S, d, _v(45, i), heartbeat=(i % 2 == 0)))  # heartbeat missing on half the days
    cases, _ = _cases(_replace(obs, new))
    assert [c["category"] for c in cases] == [UNCERTAIN]


def test_scheduled_closure_is_expected_variation():
    obs = steady_stream(S, START, 84)
    closed = _weekdays(START + timedelta(days=70), 2)
    obs = _replace(obs, [make_obs(S, d, 0, open_=False) for d in closed])
    res = pipeline.analyze(obs, SITES)
    closures = [c for c in res.cases if c["kind"] == "closure"]
    assert len(closures) == 1 and closures[0]["category"] == EXPECTED and not closures[0]["actionable"]
    assert not [c for c in res.cases if c["actionable"]]


def test_small_dip_is_not_alerted():
    obs = steady_stream(S, START, 84)
    dip = _weekdays(START + timedelta(days=70), 3)
    obs = _replace(obs, [make_obs(S, d, _v(90, i)) for i, d in enumerate(dip)])
    cases, _ = _cases(obs)
    assert not [c for c in cases if c["actionable"]]


def test_persistence_parameter_controls_alerting():
    obs = steady_stream(S, START, 84)
    one_day = START + timedelta(days=71)
    obs = _replace(obs, [make_obs(S, one_day, 20)])
    assert not _cases(obs, min_persistence=2)[0]
    assert len(_cases(obs, min_persistence=1)[0]) == 1


def test_ablation_without_feed_health_reads_missing_as_zero():
    obs = steady_stream(S, START, 84)
    gap = set(_weekdays(START + timedelta(days=70), 3))
    obs = [o for o in obs if o.service_date not in gap]
    params = DetectionParams(use_feed_health=False)
    prep = pipeline.prepare(obs, SITES, params=params)
    res = pipeline.analyze_prepared(prep, params, simple_triage=True)
    statuses = {a.date: a.status for a in res.assessments[S]}
    assert all(statuses[d] == "low" for d in gap)
    assert [c["category"] for c in res.cases if c["actionable"]] == [ACCESS]


def test_cusum_estimates_onset_of_persistent_moderate_decline():
    obs = steady_stream(S, START, 98, level=40, wiggle=4)
    gap = _weekdays(START + timedelta(days=70), 10)
    obs = _replace(obs, [make_obs(S, d, _v(30, i)) for i, d in enumerate(gap)])  # ~-25%, modest per day
    cases, res = _cases(obs, z_threshold=4.0, min_shortfall=0.25)
    assert cases, "a persistent moderate decline should be caught by the CUSUM"
    assert cases[0]["onset"] <= gap[3].isoformat()
