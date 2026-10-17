from collections import Counter
from datetime import date

from silence_engine import data_generator, evaluation
from silence_engine.evaluation import Prediction
from silence_engine.triage import ACCESS, REPORTING


def test_generator_is_deterministic_per_seed():
    a = data_generator.generate(seed=42)
    b = data_generator.generate(seed=42)
    c = data_generator.generate(seed=43)
    assert a.manifest["fingerprint_sha256"] == b.manifest["fingerprint_sha256"]
    assert a.manifest["fingerprint_sha256"] != c.manifest["fingerprint_sha256"]
    assert a.labels == b.labels


def test_generator_covers_all_scenarios_and_is_marked_synthetic(demo_dataset):
    classes = Counter(e["class"] for e in demo_dataset.labels["events"])
    assert set(classes) == set(data_generator.EVENT_CLASSES)
    assert demo_dataset.manifest["synthetic"] is True
    assert "SYNTHETIC" in demo_dataset.manifest["notice"]
    assert all(s["synthetic"] for s in demo_dataset.sites.values())
    subtypes = Counter(e["subtype"] for e in demo_dataset.labels["events"] if e["class"] == "REPORTING_FAILURE")
    assert {"dropout", "zero_fill", "stale_flatline"} <= set(subtypes)


def test_essential_services_stay_open_on_holidays(demo_dataset):
    holiday = data_generator.HOLIDAYS[0].isoformat()
    rows = {(r["site_id"], r["service_type"]): r for r in demo_dataset.raw_records if r["service_date"] == holiday}
    assert rows[("WSW", "water_distribution")]["scheduled_open"] is True
    assert rows[("RVS", "benefits_applications")]["scheduled_open"] is False


def _protocol():
    return evaluation.make_protocol(date(2026, 1, 1), date(2026, 6, 30), warmup_days=28)


def _event(eid, cls, start, end, stream="A:x"):
    return {"event_id": eid, "stream_id": stream, "class": cls, "subtype": "t", "start": start, "end": end,
            "_start": date.fromisoformat(start), "_end": date.fromisoformat(end)}


def test_scoring_separates_detection_from_explanation():
    p = _protocol()
    events = [_event("E1", "ACCESS_GAP", "2026-05-04", "2026-05-08"),
              _event("E2", "REPORTING_FAILURE", "2026-05-20", "2026-05-22"),
              _event("E3", "SCHEDULED_CLOSURE", "2026-06-01", "2026-06-01")]
    preds = [Prediction("A:x", date(2026, 5, 4), date(2026, 5, 8), date(2026, 5, 5), ACCESS, True),
             Prediction("A:x", date(2026, 5, 20), date(2026, 5, 22), date(2026, 5, 21), ACCESS, True),  # wrong category
             Prediction("A:x", date(2026, 6, 1), date(2026, 6, 1), date(2026, 6, 1), ACCESS, True)]    # false alert
    m = evaluation.score(preds, events, p, "test", {"A:x": 90})
    d, x = m["detection"], m["explanation"]
    assert d["recall"] == 1.0                      # both alertable events detected
    assert d["precision"] == round(2 / 3, 4)       # the closure alert is false
    assert d["false_alerts_by_truth"]["SCHEDULED_CLOSURE"] == 1
    assert x["accuracy_given_detection"] == 0.5    # only one explanation right
    assert x["reporting_failures_called_access_gaps"] == 1.0
    assert d["delay_days"]["median"] == 1.0


def test_metrics_respond_to_predictions_not_hardcoded():
    p = _protocol()
    events = [_event("E1", "ACCESS_GAP", "2026-05-04", "2026-05-08")]
    hit = evaluation.score([Prediction("A:x", date(2026, 5, 4), date(2026, 5, 8), date(2026, 5, 4), ACCESS, True)],
                           events, p, "test", {"A:x": 90})
    miss = evaluation.score([], events, p, "test", {"A:x": 90})
    wrong = evaluation.score([Prediction("A:x", date(2026, 5, 4), date(2026, 5, 8), date(2026, 5, 4), REPORTING, True)],
                             events, p, "test", {"A:x": 90})
    assert hit["detection"]["recall"] == 1.0 and miss["detection"]["recall"] == 0.0
    assert hit["explanation"]["accuracy_given_detection"] == 1.0
    assert wrong["explanation"]["accuracy_given_detection"] == 0.0


def test_naive_baseline_reads_missing_as_zero(demo_observations):
    params = evaluation.NaiveParams(drop_threshold=0.5, min_persistence=1)
    sample = [o for o in demo_observations if o.stream_id == "WSW:water_distribution"]
    gap_day = sample[100].service_date
    without = [o for o in sample if o.service_date != gap_day]
    preds = evaluation.naive_predictions(without, params)
    assert any(pr.onset <= gap_day <= pr.end for pr in preds)


def test_full_evaluation_report_structure(demo_dataset, demo_observations):
    rep = evaluation.evaluate(demo_observations, demo_dataset.sites, demo_dataset.labels, include_replay=False)
    assert rep["synthetic"] is True
    assert set(rep["test"]) == {"naive", "robust", "engine"}
    for method in rep["test"].values():
        cm = method["explanation"]["confusion"]
        assert len(cm["matrix"]) == len(cm["rows"]) and len(cm["matrix"][0]) == len(cm["cols"])
    assert len(rep["robustness_missingness"]) == len(evaluation.MISSINGNESS_LEVELS)
    assert len(rep["results_sha256"]) == 64
    # Same inputs -> same metrics (reproducibility).
    again = evaluation.evaluate(demo_observations, demo_dataset.sites, demo_dataset.labels, include_replay=False)
    assert again["results_sha256"] == rep["results_sha256"]


def test_evaluation_is_independent_of_record_order(demo_dataset, demo_observations):
    import random as _r

    shuffled = list(demo_observations)
    _r.Random(1).shuffle(shuffled)
    a = evaluation.evaluate(demo_observations, demo_dataset.sites, demo_dataset.labels, include_replay=False)
    b = evaluation.evaluate(shuffled, demo_dataset.sites, demo_dataset.labels, include_replay=False)
    assert a["results_sha256"] == b["results_sha256"]
