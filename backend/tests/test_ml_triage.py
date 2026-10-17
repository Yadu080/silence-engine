"""Optional ML triage benchmark.

Tests that need numpy/scikit-learn are skipped when they are not installed (as on a stock s390x Python);
the label-free feature code, the 'not available' paths and the pure helpers are tested either way.
"""

import copy
import dataclasses
import json
import math
import random
import time

import pytest

import manage
from silence_engine import database, evaluation, ml_evaluation, ml_triage, service
from silence_engine.app import create_app
from silence_engine.ml_triage import CASE_KEYS_USED, FEATURE_NAMES, MLUnavailable

needs_ml = pytest.mark.skipif(not ml_triage.availability()["available"],
                              reason="numpy / scikit-learn are not installed in this Python environment")
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
XRW = {"X-Requested-With": "SilenceEngine"}
FAST = dict(n_seeds=2, n_repeats=2, cv_splits=2)  # same predictions, cheaper diagnostics


def sample_case(**over):
    case = {
        "shortfall_frac": 0.5, "z_agg": -6.0, "open_days": 5, "history_median": 8, "mean_expected": 120.0,
        "duration_days": 6, "status_counts": {"low": 4, "missing": 1, "closed": 1},
        "flag_days": {"flatline": 2, "heartbeat_missing": 1, "stale_feed": 0},
        "context": {"severe_weather_days": 1, "transport_days": 0, "outage_days": 0},
        "peers": {"compared": 3, "declining": 1},
        # the rule engine's own verdicts: the model must never read these
        "category": "POTENTIAL_ACCESS_GAP", "reason_code": "strong_decline_healthy_feed", "priority": 90,
        "evidence_score": 80, "headline": "x",
    }
    case.update(over)
    return case


class Recording(dict):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.touched = set()

    def __getitem__(self, key):
        self.touched.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.touched.add(key)
        return super().get(key, default)


# ----------------------------------------------------------------------------
# Always-on tests (standard library only)
# ----------------------------------------------------------------------------

def test_features_read_only_evidence_never_the_rule_verdict():
    case = Recording(sample_case())
    row = ml_triage.featurize(case)
    assert len(row) == len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
    assert all(isinstance(v, float) and math.isfinite(v) for v in row)
    assert case.touched <= CASE_KEYS_USED
    for verdict_key in ("category", "reason_code", "priority", "evidence_score", "headline", "actionable"):
        assert verdict_key not in case.touched
        assert verdict_key not in FEATURE_NAMES


def test_feature_values_and_unmeasurable_decline():
    f = dict(zip(FEATURE_NAMES, ml_triage.featurize(sample_case())))
    assert f["shortfall_frac"] == 0.5 and f["z_agg"] == -6.0 and f["has_trusted_days"] == 1.0
    assert f["feed_problem_share"] == pytest.approx(1 / 5) and f["missing_share"] == pytest.approx(1 / 5)
    assert f["low_share"] == pytest.approx(4 / 5) and f["flag_flatline_share"] == pytest.approx(2 / 5)
    assert f["severe_weather"] == 1.0 and f["transport_disruption"] == 0.0 and f["peer_declining"] == 1.0
    g = dict(zip(FEATURE_NAMES, ml_triage.featurize(sample_case(shortfall_frac=None, z_agg=None, mean_expected=None))))
    assert g["has_trusted_days"] == 0.0 and g["shortfall_frac"] == 0.0 and g["z_agg"] == 0.0 and g["mean_expected"] == 0.0


def test_mcnemar_exact_p():
    assert ml_evaluation.mcnemar_exact_p(0, 0) == 1.0
    assert ml_evaluation.mcnemar_exact_p(2, 2) == 1.0
    assert ml_evaluation.mcnemar_exact_p(0, 5) == pytest.approx(0.0625)
    assert ml_evaluation.mcnemar_exact_p(1, 9) == pytest.approx(0.0215, abs=1e-4)


def test_clear_reason_when_ml_stack_is_missing(monkeypatch, demo_dataset, demo_observations):
    monkeypatch.setattr(ml_triage, "_probe_cache", {"available": False, "reason": "forced: scikit-learn missing"})
    status = ml_triage.availability()
    assert status["available"] is False and "forced" in status["reason"] and "packages" in status
    with pytest.raises(MLUnavailable):
        ml_triage.require()
    with pytest.raises(MLUnavailable):
        ml_evaluation.evaluate_ml(demo_observations, demo_dataset.sites, demo_dataset.labels)


def test_api_reports_not_available_instead_of_failing(monkeypatch, initialised_settings):
    monkeypatch.setattr(ml_triage, "_probe_cache", {"available": False, "reason": "forced: scikit-learn missing"})
    client = create_app(initialised_settings, start_background=False).test_client()
    body = client.get("/api/ml-triage").get_json()
    assert body["available"] is False and "forced" in body["reason"] and "report" not in body
    r = client.post("/api/ml-triage/run", environ_base=LOCAL, headers=XRW)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "ml_unavailable"
    # the core API is untouched
    assert client.get("/api/health").get_json()["status"] == "ok"
    assert client.get("/api/evaluation").get_json()["available"] is True


def test_cli_exits_with_message_when_ml_stack_is_missing(monkeypatch, capsys, initialised_settings):
    monkeypatch.setattr(ml_triage, "_probe_cache", {"available": False, "reason": "forced: scikit-learn missing"})
    monkeypatch.setenv("SILENCE_DATA_DIR", str(initialised_settings.data_dir))
    assert manage.main(["evaluate-ml"]) == 3
    assert "not available" in capsys.readouterr().err


def test_ml_can_be_switched_off(make_settings):
    reason = service.ml_unavailable_reason(make_settings(SILENCE_ML_TRIAGE="0"))
    assert reason and "SILENCE_ML_TRIAGE" in reason


def test_system_info_lists_ml_packages_without_importing_them(client):
    info = client.get("/api/system-info").get_json()["runtime"]
    assert set(info["ml_packages"]) >= {"scikit-learn", "numpy"}


# ----------------------------------------------------------------------------
# Tests that need scikit-learn
# ----------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tuned_params(initialised_settings):
    with service.connection(initialised_settings) as conn:
        return service.current_params(conn)


@pytest.fixture(scope="session")
def ml_report(demo_dataset, demo_observations, tuned_params):
    return ml_evaluation.evaluate_ml(demo_observations, demo_dataset.sites, demo_dataset.labels, tuned_params)


@pytest.fixture(scope="session")
def protocol(demo_observations):
    return evaluation.make_protocol(min(o.service_date for o in demo_observations),
                                    max(o.service_date for o in demo_observations))


@needs_ml
def test_training_set_cannot_see_the_test_period(demo_dataset, demo_observations, tuned_params, protocol):
    events = evaluation.normalise_labels(demo_dataset.labels)["events"]
    cases, X, y = ml_evaluation.build_training_set(demo_observations, demo_dataset.sites, events, protocol, tuned_params)
    assert len(X) > 20 and len(set(y)) >= 2
    assert all(c["alert_date"] <= protocol.train_end.isoformat() for c in cases)
    # Destroy every record after the training period and scramble every label that starts after it.
    ruined = [dataclasses.replace(o, observed_count=0, value_status="ok") if o.service_date > protocol.train_end else o
              for o in demo_observations]
    rng = random.Random(1)
    later = [e for e in events if e["_start"] > protocol.train_end]
    classes = [e["class"] for e in later]
    rng.shuffle(classes)
    scrambled = [dict(e) for e in events]
    for e in scrambled:
        if e["_start"] > protocol.train_end:
            e["class"] = classes.pop()
    cases2, X2, y2 = ml_evaluation.build_training_set(ruined, demo_dataset.sites, scrambled, protocol, tuned_params)
    assert [c["case_id"] for c in cases2] == [c["case_id"] for c in cases]
    assert X2 == X and y2 == y


@needs_ml
def test_test_labels_cannot_change_the_models_predictions(demo_dataset, demo_observations, tuned_params, protocol):
    labels = demo_dataset.labels
    base = ml_evaluation.evaluate_ml(demo_observations, demo_dataset.sites, labels, tuned_params, **FAST)
    no_test_labels = copy.deepcopy(labels)
    no_test_labels["events"] = [e for e in labels["events"] if e["start"] <= protocol.train_end.isoformat()]
    changed = ml_evaluation.evaluate_ml(demo_observations, demo_dataset.sites, no_test_labels, tuned_params, **FAST)
    assert changed["predictions_sha256"] == base["predictions_sha256"]
    assert changed["model"]["spec"] == base["model"]["spec"]
    assert changed["training"] == base["training"]
    assert changed["test"]["ml"]["detection"]["alertable_events"] == 0  # the metrics, unlike the predictions, do depend on labels


@needs_ml
def test_run_is_reproducible_and_hash_matches_content(ml_report, demo_dataset, demo_observations, tuned_params):
    again = ml_evaluation.evaluate_ml(demo_observations, demo_dataset.sites, demo_dataset.labels, tuned_params)
    assert again["results_sha256"] == ml_report["results_sha256"]
    assert again["predictions_sha256"] == ml_report["predictions_sha256"]
    assert ml_evaluation.results_hash(ml_report) == ml_report["results_sha256"]
    json.dumps(ml_report)  # plain JSON types only (no numpy scalars)


@needs_ml
def test_rule_side_of_the_comparison_equals_the_main_evaluation(ml_report, demo_dataset, demo_observations):
    main = evaluation.evaluate(demo_observations, demo_dataset.sites, demo_dataset.labels, include_replay=False)["test"]["engine"]
    rules = ml_report["test"]["rules"]
    assert rules["explanation"] == main["explanation"]
    assert rules["detection"] == main["detection"]


@needs_ml
def test_report_structure_and_honest_framing(ml_report, protocol):
    r = ml_report
    assert r["synthetic"] is True and "SYNTHETIC" in r["notice"]
    assert r["training"]["window"] == [protocol.train_start.isoformat(), protocol.train_end.isoformat()]
    assert r["test"]["window"] == [protocol.test_start.isoformat(), protocol.test_end.isoformat()]
    assert r["production_classifier"]["name"] == "rule-based triage"
    assert "never a feature" in r["model"]["inputs_excluded"]
    assert [i["feature"] for i in r["importances"]] != [] and {i["feature"] for i in r["importances"]} == set(FEATURE_NAMES)
    assert r["conclusion"]["verdict"] in {"no_detectable_difference", "ml_better", "rules_better", "inconclusive"}
    p = r["test"]["paired"]
    assert p["both_correct"] + p["rules_only_correct"] + p["ml_only_correct"] + p["both_wrong"] == p["episodes"] == r["test"]["episodes"]
    assert len(r["disagreements"]) >= p["rules_only_correct"] + p["ml_only_correct"]
    for d in r["disagreements"]:
        assert d["rule_category"] != d["ml_category"] and d["correct"] in {"rules", "ml", "neither"}
    # the model learned something from real features, not from labels it should not see
    ctl = r["sensitivity"]["shuffled_label_control"]
    assert ctl["accuracy_mean"] < p["ml"]["accuracy"] - 0.15
    text = ml_evaluation.format_comparison(r)
    assert "Verdict:" in text and "Production classifier: rule-based triage" in text and r["results_sha256"] in text


@needs_ml
def test_running_the_benchmark_never_changes_rule_based_results(initialised_settings):
    def snapshot():
        with service.connection(initialised_settings) as conn:
            return (json.dumps(database.load_cases(conn), sort_keys=True), database.current_evaluation(conn)["results_sha256"],
                    database.current_run(conn)["run_id"])
    before = snapshot()
    out = service.run_ml_evaluation(initialised_settings, actor="test")
    assert out["results_sha256"]
    assert snapshot() == before
    with service.connection(initialised_settings) as conn:
        assert service.current_ml_report(conn)["results_sha256"] == out["results_sha256"]
        assert audit_has(conn, "ml_triage_evaluated")


def audit_has(conn, action):
    return conn.execute("SELECT 1 FROM audit_log WHERE action = ?", (action,)).fetchone() is not None


@needs_ml
def test_api_serves_report_and_runs_job(initialised_settings, make_settings):
    app = create_app(initialised_settings, start_background=False)
    client = app.test_client()
    body = client.get("/api/ml-triage").get_json()
    assert body["available"] is True and body["report"]["results_sha256"] and body["stale"] is False
    assert body["report"]["production_classifier"]["name"] == "rule-based triage"
    # a database with nothing loaded: available, but not run
    empty = create_app(make_settings(), start_background=False).test_client().get("/api/ml-triage").get_json()
    assert empty["available"] is True and empty["report"] is None and empty["reason"]
    # writes need the local-client + CSRF header
    assert client.post("/api/ml-triage/run", environ_base=LOCAL).status_code == 403
    r = client.post("/api/ml-triage/run", environ_base=LOCAL, headers=XRW)
    assert r.status_code == 202
    job_id = r.get_json()["job"]["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "succeeded", job
    assert job["result"]["results_sha256"] == client.get("/api/ml-triage").get_json()["report"]["results_sha256"]


@needs_ml
def test_cli_prints_the_side_by_side_comparison(monkeypatch, capsys, initialised_settings, tmp_path):
    monkeypatch.setenv("SILENCE_DATA_DIR", str(initialised_settings.data_dir))
    out = tmp_path / "ml.json"
    assert manage.main(["evaluate-ml", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Rules" in text and "Explanation accuracy given detection" in text and "Where they disagree" in text
    assert json.loads(out.read_text())["results_sha256"] in text


@needs_ml
def test_too_little_training_data_is_refused_not_faked(demo_dataset, demo_observations, tuned_params):
    one_stream = [o for o in demo_observations if o.stream_id == demo_observations[0].stream_id]
    with pytest.raises(ml_evaluation.InsufficientTrainingData):
        ml_evaluation.evaluate_ml(one_stream, demo_dataset.sites, demo_dataset.labels, tuned_params, **FAST)
