from datetime import date, datetime, time, timedelta, timezone

from silence_engine import data_quality
from tests.conftest import make_obs, steady_stream

S = "SITE:clinic"
START = date(2026, 1, 5)  # Monday


def _days(obs):
    return data_quality.prepare_streams(obs)[S].days


def test_absent_record_stays_missing_not_zero():
    obs = steady_stream(S, START, 21)
    gap_day = START + timedelta(days=15)  # a Tuesday
    obs = [o for o in obs if o.service_date != gap_day]
    rec = next(r for r in _days(obs) if r.date == gap_day)
    assert rec.observed is None
    assert rec.value_status == "missing_record"
    assert "missing_record" in rec.flags
    assert rec.scheduled_open and rec.schedule_inferred  # inferred from the weekday pattern
    assert not rec.trusted


def test_zero_with_feed_down_is_suspect_but_genuine_zero_is_trusted():
    obs = steady_stream(S, START, 14)
    d1, d2 = START + timedelta(days=7), START + timedelta(days=8)
    obs = [o for o in obs if o.service_date not in (d1, d2)]
    obs.append(make_obs(S, d1, 0, heartbeat=False, received=0))   # placeholder zero
    obs.append(make_obs(S, d2, 0, heartbeat=True))                # observed zero, feed healthy
    days = {r.date: r for r in _days(obs)}
    assert "zero_fill_suspected" in days[d1].flags and not days[d1].trusted
    assert days[d2].observed == 0 and days[d2].trusted


def test_flatline_detected_at_high_volume_only_after_enough_repeats():
    obs = steady_stream(S, START, 28, level=200)
    flat_start = START + timedelta(days=21)  # Monday of week 4
    flat_days = [flat_start + timedelta(days=i) for i in range(5)]
    obs = [o for o in obs if o.service_date not in flat_days] + [make_obs(S, d, 187) for d in flat_days]
    days = {r.date: r for r in _days(obs)}
    flagged = [d for d in flat_days if "flatline" in days[d].flags]
    assert flagged, "a 5-day run of identical counts at ~200/day should be flagged"
    assert all(not days[d].trusted for d in flagged)
    # the flag is retroactive, but records when it became knowable (causality)
    assert all(days[d].distrust_known_on >= d for d in flagged)
    assert all("flatline" not in days[d].flags for d in days if d < flat_start)


def test_duplicates_resolved_to_latest_and_conflicts_flagged():
    obs = steady_stream(S, START, 10)
    d = START + timedelta(days=2)
    later = make_obs(S, d, 55)
    later.report_received_at = datetime.combine(d, time(23, 30), tzinfo=timezone.utc)
    rec = next(r for r in _days(obs + [later]) if r.date == d)
    assert rec.observed == 55 and rec.duplicate_rows == 1 and "duplicate_conflict" in rec.flags


def test_stale_feed_and_incomplete_batches():
    obs = steady_stream(S, START, 10)
    d = START + timedelta(days=3)
    obs = [o for o in obs if o.service_date != d]
    obs.append(make_obs(S, d, 90, received=2, last_seen=datetime.combine(d - timedelta(days=3), time(18), tzinfo=timezone.utc)))
    rec = next(r for r in _days(obs) if r.date == d)
    assert "stale_feed" in rec.flags and "incomplete_batches" in rec.flags and not rec.trusted


def test_activity_on_scheduled_closed_day_flagged():
    obs = steady_stream(S, START, 21)
    d = START + timedelta(days=16)  # Wednesday
    obs = [o for o in obs if o.service_date != d] + [make_obs(S, d, 97, open_=False)]
    rec = next(r for r in _days(obs) if r.date == d)
    assert "activity_on_closed_day" in rec.flags


def test_visibility_hides_late_records():
    obs = steady_stream(S, START, 10)
    d = START + timedelta(days=8)
    obs = [o for o in obs if o.service_date != d] + [make_obs(S, d, 99, delay_days=2)]
    visible_at = datetime.combine(d, time(23, 59), tzinfo=timezone.utc)
    rec = next(r for r in data_quality.prepare_streams(obs, end_date=d, visible_at=visible_at)[S].days if r.date == d)
    assert rec.observed is None and rec.value_status == "missing_record"


def test_issue_runs_and_health_summary():
    obs = steady_stream(S, START, 28)
    drop = {START + timedelta(days=i) for i in (22, 23, 24)}  # Tue-Thu of week 4
    obs = [o for o in obs if o.service_date not in drop]
    streams = data_quality.prepare_streams(obs)
    issues = data_quality.collect_issues(streams)
    missing = [i for i in issues if i["type"] == "missing_record"]
    assert len(missing) == 1 and missing[0]["days"] == 3
    health = data_quality.stream_health(streams[S], as_of=START + timedelta(days=27))
    assert health["status"] in ("DEGRADED", "FAILED")
    assert health["counts_total"]["missing_record"] == 3
