"""Evaluation side of the optional ML triage benchmark (SYNTHETIC data with known ground truth).

Like ``evaluation.py`` this is an evaluation-style module: it is allowed to read ground-truth labels, which
it uses only to (a) build training targets from the synthetic TRAINING period and (b) score predictions on
the held-out TEST period. Labels never reach the detector, the pipeline, or ``ml_triage`` (which is
label-free). ``tests/test_leakage.py`` and ``tests/test_ml_triage.py`` enforce this.

Protocol (same periods as ``evaluation.make_protocol``)
  * The detector (thresholds already tuned on the training period by ``evaluation``) flags episodes.
    Both triage methods then explain the SAME flagged episodes, so differences come from triage alone.
  * Training set: a pipeline run over records up to the end of the training period only, episodes whose
    alert date is in the training period, targets from events that START in the training period.
    Nothing dated after the training period (records or labels) can influence the model.
  * Test: a pipeline run over all records; episodes whose alert date is in the test period. The rule-based
    triage and the model are scored with the very same ``evaluation.score`` function and confidence intervals.
  * Hyper-parameters are fixed in advance (``ml_triage.ModelSpec``); nothing is tuned on the test period.
  * Scheduled-closure episodes are decided by the schedule (a rule, not a learning problem) in both methods.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform as _platform
import random
import sys
import time
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import ENGINE_VERSION, evaluation, ml_triage, pipeline
from .detection import DetectionParams
from .evaluation import ALERTABLE, CORRECT
from .ingest import Observation
from .ml_triage import FEATURE_DESCRIPTIONS, FEATURE_NAMES, MLUnavailable, ModelSpec, TriageModel
from .triage import ACCESS, EXPECTED, REASON_TEXT, REPORTING, UNCERTAIN, TriageParams

TRUTH_TO_CATEGORY = {"ACCESS_GAP": ACCESS, "REPORTING_FAILURE": REPORTING, "AMBIGUOUS": UNCERTAIN}
SHORT = {ACCESS: "access gap", REPORTING: "reporting problem", UNCERTAIN: "uncertain", EXPECTED: "expected variation"}
VOLATILE_KEYS = ("duration_seconds", "results_sha256", "generated_at", "platform", "runtime")
MIN_TRAIN_EPISODES = 12
NOTICE = ("All results are computed on SYNTHETIC data with known ground truth. The detector flags the episodes; the rule-based "
          "triage and the ML model then explain the same episodes. With a training set this small, differences of a few "
          "episodes are not meaningful.")


PRODUCTION_REASON = {
    "no_detectable_difference": "The ML model showed no detectable advantage, and the rules are deterministic and explain every decision "
                                "with named evidence. The model is an optional second opinion and never changes the cases in the queue.",
    "ml_better": "The ML model scored higher on this small synthetic sample, but the rules are deterministic and explain every decision "
                 "with named evidence, so they stay the default until the gain is confirmed on more (and real) data.",
    "rules_better": "The rules scored higher, and they are deterministic and explain every decision with named evidence. The model is an "
                    "optional second opinion and never changes the cases in the queue.",
    "inconclusive": "Too few detected events to compare, so nothing justifies replacing the deterministic, explainable rules.",
}


class InsufficientTrainingData(ValueError):
    """The training period produced too few (or only one class of) episodes to fit a model."""


# ----------------------------------------------------------------------------
# Episodes, truth and targets
# ----------------------------------------------------------------------------

def silence_cases(cases: Sequence[dict], lo: date, hi: date) -> List[dict]:
    """Silence episodes (not scheduled closures) whose alert date falls inside [lo, hi]."""
    return [c for c in cases if c["kind"] == "silence" and c["alert_date"]
            and lo <= date.fromisoformat(c["alert_date"]) <= hi]


def _best_event(case: dict, events_by_stream: Dict[str, List[dict]]) -> Optional[dict]:
    pred = evaluation.predictions_from_cases([case])[0]
    best, best_ov = None, 0
    for e in events_by_stream.get(case["stream_id"], ()):
        ov = evaluation._overlaps(pred, e)
        if ov > best_ov:
            best, best_ov = e, ov
    return best


def _index(events: Sequence[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for e in events:
        out.setdefault(e["stream_id"], []).append(e)
    return out


def target_for(case: dict, events_by_stream: Dict[str, List[dict]]) -> Tuple[str, Optional[dict]]:
    """Triage category matching the synthetic event the episode overlaps most (EXPECTED if none/benign)."""
    ev = _best_event(case, events_by_stream)
    return (TRUTH_TO_CATEGORY.get(ev["class"], EXPECTED) if ev else EXPECTED), ev


def build_training_set(observations: Sequence[Observation], sites: Dict[str, dict], events: Sequence[dict],
                       protocol: evaluation.Protocol, params: DetectionParams
                       ) -> Tuple[List[dict], List[List[float]], List[str]]:
    """Feature rows and targets from the TRAINING period only.

    The pipeline is run on records dated up to the end of the training period, and only events that start
    inside the training period are used as labels, so no test-period record or label can reach the model.
    """
    train_obs = [o for o in observations if o.service_date <= protocol.train_end]
    result = pipeline.analyze(train_obs, sites, params, TriageParams())
    train_events = _index([e for e in events if protocol.period_of(e["_start"]) == "train"])
    cases = silence_cases(result.cases, protocol.train_start, protocol.train_end)
    rows = [ml_triage.featurize(c) for c in cases]
    targets = [target_for(c, train_events)[0] for c in cases]
    return cases, rows, targets


# ----------------------------------------------------------------------------
# Small statistics helpers
# ----------------------------------------------------------------------------

def mcnemar_exact_p(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value from the two discordant counts."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def _acc(hits: int, n: int) -> dict:
    lo, hi = evaluation.stats.wilson_interval(hits, n)
    return {"correct": hits, "episodes": n, "accuracy": evaluation._rate(hits, n),
            "ci": [evaluation.stats.round_or_none(lo, 4), evaluation.stats.round_or_none(hi, 4)]}


def _counts(values: Sequence[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _r(x: float, digits: int = 4) -> float:
    return round(float(x), digits)


def _with_category(cases: Sequence[dict], overrides: Dict[str, str]) -> List[dict]:
    """Copies of the cases with the category of silence episodes replaced (closures keep the schedule rule)."""
    out = []
    for c in cases:
        c2 = dict(c)
        if c["case_id"] in overrides:
            c2["category"] = overrides[c["case_id"]]
            c2["actionable"] = c2["category"] != EXPECTED
        out.append(c2)
    return out


# ----------------------------------------------------------------------------
# The benchmark
# ----------------------------------------------------------------------------

def evaluate_ml(observations: Sequence[Observation], sites: Dict[str, dict], labels: dict,
                params: Optional[DetectionParams] = None, spec: ModelSpec = ModelSpec(),
                progress: Optional[Callable[[str], None]] = None, n_seeds: int = 10, n_repeats: int = 20,
                cv_splits: int = 5) -> dict:
    """Train on the synthetic training period, evaluate on the test period, compare with the rules."""
    ml_triage.require()
    t0 = time.time()

    def step(msg: str) -> None:
        if progress:
            progress(msg)

    labels = evaluation.normalise_labels(labels)
    label_streams = set(labels.get("streams", []))
    obs = [o for o in observations if o.stream_id in label_streams]
    events = labels["events"]
    protocol = evaluation.make_protocol(min(o.service_date for o in obs), max(o.service_date for o in obs))

    prep = pipeline.prepare(obs, sites, params=params or DetectionParams())
    params_source = "supplied (thresholds tuned on the training period by the evaluation harness)"
    if params is None:
        step("tuning detector thresholds on the training period")
        params, _grid = evaluation.tune("engine", {"engine": prep}, obs, events, protocol)
        params_source = "tuned here on the training period (evaluation.tune)"
        prep = pipeline.prepare(obs, sites, params=params)

    step("training set: episodes from the training period only")
    train_cases, X_train, y_train = build_training_set(obs, sites, events, protocol, params)
    if len(X_train) < MIN_TRAIN_EPISODES or len(set(y_train)) < 2:
        raise InsufficientTrainingData(
            f"The training period produced {len(X_train)} flagged episode(s) in {len(set(y_train))} class(es); "
            f"at least {MIN_TRAIN_EPISODES} episodes in two classes are needed to fit a model.")

    step("detecting and triaging the full period with the rules")
    result = pipeline.analyze_prepared(prep, params, TriageParams())
    test_cases = silence_cases(result.cases, *protocol.bounds("test"))
    all_events = _index(events)
    X_test = [ml_triage.featurize(c) for c in test_cases]
    truths = [target_for(c, all_events) for c in test_cases]
    y_test = [t for t, _ in truths]

    step(f"fitting {spec.kind.replace('_', ' ')} on {len(X_train)} training episodes")
    model = TriageModel(spec).fit(X_train, y_train)
    cv = ml_triage.cross_validated_accuracy(X_train, y_train, spec, cv_splits)

    # Predictions for every silence episode of the full run (closures keep the schedule rule).
    silence_all = [c for c in result.cases if c["kind"] == "silence"]
    ml_all = dict(zip((c["case_id"] for c in silence_all), model.predict([ml_triage.featurize(c) for c in silence_all])))
    proba_test = model.predict_proba(X_test)
    ml_test = [ml_all[c["case_id"]] for c in test_cases]

    step("scoring the test period with the same metrics for both methods")
    tiers = evaluation.volume_tiers(prep, protocol)
    stream_days = evaluation._stream_days(prep, protocol, "test")
    rule_metrics = evaluation.score(evaluation.predictions_from_cases(result.cases), events, protocol, "test", stream_days, tiers)
    ml_metrics = evaluation.score(evaluation.predictions_from_cases(_with_category(result.cases, ml_all)),
                                  events, protocol, "test", stream_days, tiers)

    # Paired, episode-level comparison.
    rule_test = [c["category"] for c in test_cases]
    rule_ok = [r == t for r, t in zip(rule_test, y_test)]
    ml_ok = [m == t for m, t in zip(ml_test, y_test)]
    both = sum(1 for a, b in zip(rule_ok, ml_ok) if a and b)
    only_rule = sum(1 for a, b in zip(rule_ok, ml_ok) if a and not b)
    only_ml = sum(1 for a, b in zip(rule_ok, ml_ok) if b and not a)
    neither = len(rule_ok) - both - only_rule - only_ml
    paired = {"episodes": len(test_cases), "both_correct": both, "rules_only_correct": only_rule,
              "ml_only_correct": only_ml, "both_wrong": neither, "mcnemar_exact_p": mcnemar_exact_p(only_rule, only_ml),
              "rules": _acc(sum(rule_ok), len(rule_ok)), "ml": _acc(sum(ml_ok), len(ml_ok))}

    step("feature importances and sensitivity checks")
    impurity = model.impurity_importances()
    permutation = model.permutation_importances(X_test, y_test, n_repeats=n_repeats, seed=spec.seed)
    importances = sorted(
        ({"feature": f, "description": FEATURE_DESCRIPTIONS[f], "impurity": _r(impurity[f]),
          "permutation_mean": _r(permutation[f][0]), "permutation_std": _r(permutation[f][1])} for f in FEATURE_NAMES),
        key=lambda d: (-d["permutation_mean"], -d["impurity"], d["feature"]))

    def test_accuracy(m: TriageModel) -> Tuple[int, int]:
        return sum(1 for p, t in zip(m.predict(X_test), y_test) if p == t), len(y_test)

    seed_hits = [test_accuracy(TriageModel(ModelSpec(spec.kind, spec.n_estimators, spec.max_depth, spec.min_samples_leaf,
                                                     spec.learning_rate, s)).fit(X_train, y_train))[0] for s in range(n_seeds)]
    gb = TriageModel(ModelSpec("gradient_boosting", spec.n_estimators, spec.max_depth, spec.min_samples_leaf,
                               spec.learning_rate, spec.seed)).fit(X_train, y_train)
    majority_class = sorted(_counts(y_train).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    shuffled_hits = []
    for s in range(5):
        shuffled = list(y_train)
        random.Random(s).shuffle(shuffled)
        shuffled_hits.append(test_accuracy(TriageModel(spec).fit(X_train, shuffled))[0])
    n_test = len(y_test)
    sensitivity = {
        "other_seeds": {"seeds": list(range(n_seeds)), "correct": seed_hits, "episodes": n_test,
                        "accuracy_min": evaluation._rate(min(seed_hits), n_test), "accuracy_max": evaluation._rate(max(seed_hits), n_test),
                        "note": "Same hyper-parameters, different random seeds. The headline model uses the fixed seed "
                                f"{spec.seed}; it was not chosen among these."},
        "gradient_boosting": {**_acc(test_accuracy(gb)[0], n_test),
                              "note": "Reported as a sensitivity check; the random forest is the headline model."},
        "majority_class": {**_acc(sum(1 for t in y_test if t == majority_class), n_test), "class": majority_class,
                           "note": "Always predict the most common training class."},
        "shuffled_label_control": {"correct": shuffled_hits, "episodes": n_test,
                                   "accuracy_mean": evaluation._rate(sum(shuffled_hits), 5 * n_test),
                                   "accuracy_max": evaluation._rate(max(shuffled_hits), n_test),
                                   "note": "Model trained on randomly permuted training targets. Accuracy near the "
                                           "majority-class rate means no label information leaks into the features."},
    }

    disagreements = []
    for c, row, rule_cat, ml_cat, (truth_cat, ev), proba in zip(test_cases, X_test, rule_test, ml_test, truths, proba_test):
        if rule_cat == ml_cat:
            continue
        disagreements.append({
            "case_id": c["case_id"], "site_name": c["site_name"], "service_type": c["service_type"],
            "onset": c["onset"], "end": c["end"], "feed_status": c["feed_status"],
            "truth_class": ev["class"] if ev else "NO_EVENT", "truth_subtype": ev["subtype"] if ev else None,
            "truth_category": truth_cat, "rule_category": rule_cat, "rule_reason_code": c["reason_code"],
            "rule_reason": REASON_TEXT.get(c["reason_code"], ""), "ml_category": ml_cat,
            "ml_probabilities": {k: _r(v, 3) for k, v in sorted(proba.items())},
            "ml_top_features": model.explain(row),
            "correct": "rules" if rule_cat == truth_cat else "ml" if ml_cat == truth_cat else "neither",
        })

    rule_exp, ml_exp = rule_metrics["explanation"], ml_metrics["explanation"]
    conclusion = _conclusion(rule_exp, ml_exp, paired, sensitivity, len(X_train))
    protocol_d = protocol.to_dict()
    predictions_sha = hashlib.sha256(json.dumps(sorted([c["case_id"], m] for c, m in zip(test_cases, ml_test))).encode()).hexdigest()

    report = {
        "synthetic": True, "notice": NOTICE, "engine_version": ENGINE_VERSION,
        "dataset_fingerprint": labels.get("dataset_fingerprint_sha256"),
        "protocol": protocol_d,
        "detector": {"params": params.to_dict(), "source": params_source,
                     "episode_scope": "Silence episodes that reached the detector's persistence rule, assigned to a period by "
                                      "alert date. Scheduled-closure episodes are decided by the schedule in both methods."},
        "model": {"name": "RandomForestClassifier" if spec.kind == "random_forest" else "GradientBoostingClassifier",
                  "spec": spec.to_dict(), "features": [{"name": f, "description": FEATURE_DESCRIPTIONS[f]} for f in FEATURE_NAMES],
                  "classes": list(model.classes), "target": "Triage category matching the synthetic event class "
                  "(access gap / reporting failure / ambiguous); episodes with no alertable event would map to expected variation.",
                  "inputs_excluded": "The rule-based verdict (category, reason code, priority, evidence score) is never a feature."},
        "training": {"episodes": len(X_train), "by_target": _counts(y_train),
                     "window": [protocol_d["train_start"], protocol_d["train_end"]],
                     "built_from": "Records dated up to the end of the training period; events starting in the training period.",
                     "cv_accuracy": ({"folds": [_r(f) for f in cv], "mean": _r(sum(cv) / len(cv))} if cv else None),
                     "in_sample_accuracy": evaluation._rate(sum(1 for p, t in zip(model.predict(X_train), y_train) if p == t), len(y_train)),
                     "in_sample_note": "Optimistic by construction (the model has seen these episodes)."},
        "test": {"window": [protocol_d["test_start"], protocol_d["test_end"]], "episodes": n_test,
                 "by_target": _counts(y_test), "rules": rule_metrics, "ml": ml_metrics, "paired": paired},
        "importances": importances,
        "sensitivity": sensitivity,
        "disagreements": disagreements,
        "conclusion": conclusion,
        "production_classifier": {"name": "rule-based triage", "reason": PRODUCTION_REASON[conclusion["verdict"]]},
        "predictions_sha256": predictions_sha,
        "runtime": {"machine": _platform.machine(), "system": _platform.system(), "python": _platform.python_version(),
                    "byteorder": sys.byteorder, "libraries": {k: v for k, v in ml_triage.package_versions().items() if v}},
    }
    report["results_sha256"] = results_hash(report)
    report["duration_seconds"] = round(time.time() - t0, 2)
    return report


def results_hash(report: dict) -> str:
    """Hash of the metric content only (no timings, hostnames or library versions)."""
    content = {k: v for k, v in report.items() if k not in VOLATILE_KEYS}
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _conclusion(rule_exp: dict, ml_exp: dict, paired: dict, sensitivity: dict, n_train: int) -> dict:
    ra, ma = rule_exp["accuracy_given_detection"], ml_exp["accuracy_given_detection"]
    p = paired["mcnemar_exact_p"]
    if ra is None or ma is None:
        verdict, headline = "inconclusive", "Too few detected events to compare."
    elif p >= 0.05:
        verdict = "no_detectable_difference"
        headline = (f"No detectable difference. Explanation accuracy given detection: rules {ra:.0%}, ML {ma:.0%}. "
                    f"Only the rules are right on {paired['rules_only_correct']} of {paired['episodes']} episodes, only the ML model "
                    f"on {paired['ml_only_correct']} (exact McNemar p = {p:.2f}).")
    elif ma > ra:
        verdict, headline = "ml_better", f"The ML model explains more detected events correctly ({ma:.0%} vs {ra:.0%}, p = {p:.3f})."
    else:
        verdict, headline = "rules_better", f"The rules explain more detected events correctly ({ra:.0%} vs {ma:.0%}, p = {p:.3f})."
    points = [
        f"The model was trained on only {n_train} flagged episodes from the synthetic training period, so a single episode moves "
        "its accuracy by about two percentage points.",
        "Both methods use the same per-episode evidence (the model is not given the rules' verdict), so the ML model is expected "
        "to rediscover thresholds the rules already encode; the importance tables show which signals it leaned on.",
        "ML does not decide what is flagged: the detector does. The model cannot raise recall or remove false alerts.",
        "Synthetic data from the same team's generator: neither method's accuracy says anything about real services.",
    ]
    if verdict == "ml_better":
        points.append("Even so, the rule-based triage stays the dashboard default because it is deterministic and every "
                      "decision is explained by named evidence.")
    return {"verdict": verdict, "headline": headline, "points": points}


# ----------------------------------------------------------------------------
# Plain-text rendering (CLI and notebook)
# ----------------------------------------------------------------------------

def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def format_comparison(r: dict) -> str:
    rt, mt, paired = r["test"]["rules"], r["test"]["ml"], r["test"]["paired"]
    L: List[str] = []
    rt_ = r.get("runtime", {})
    libs = ", ".join(f"{k} {v}" for k, v in rt_.get("libraries", {}).items() if k in ("scikit-learn", "numpy", "scipy", "xgboost", "pandas"))
    L.append("ML triage benchmark on SYNTHETIC data — rule-based triage vs machine learning")
    L.append(f"  machine: {rt_.get('machine')} ({rt_.get('byteorder')}-endian), Python {rt_.get('python')}; {libs}")
    m = r["model"]
    L.append(f"  model:   {m['name']}(" + ", ".join(f"{k}={v}" for k, v in m["spec"].items()) + ")")
    L.append(f"  inputs:  {len(m['features'])} features per flagged episode, e.g. " + ", ".join(f["name"] for f in m["features"][:6]) + ", …")
    t = r["training"]
    L.append(f"  trained: {t['episodes']} episodes ({', '.join(f'{SHORT[k]} {v}' for k, v in t['by_target'].items())}) "
             f"up to {t['window'][1]}; 5-fold CV accuracy " + (_pct(t['cv_accuracy']['mean']) if t['cv_accuracy'] else "n/a"))
    L.append(f"  tested:  {r['test']['episodes']} episodes {r['test']['window'][0]} → {r['test']['window'][1]} "
             f"({', '.join(f'{SHORT[k]} {v}' for k, v in r['test']['by_target'].items())})")
    L.append("")
    L.append(f"{'TEST PERIOD':<44}{'Rules':>16}{'ML':>16}")

    def row(label: str, a: str, b: str) -> None:
        L.append(f"{label:<44}{a:>16}{b:>16}")

    ra, ma = rt["explanation"], mt["explanation"]
    row("Explanation accuracy given detection", f"{_pct(ra['accuracy_given_detection'])} ({ra['correct']}/{ra['detected']})",
        f"{_pct(ma['accuracy_given_detection'])} ({ma['correct']}/{ma['detected']})")
    lo, hi = ra["accuracy_ci"], ma["accuracy_ci"]
    row("  95% Wilson interval", f"{_pct(lo[0])}–{_pct(lo[1])}", f"{_pct(hi[0])}–{_pct(hi[1])}")
    row("Event recall", _pct(rt["detection"]["recall"]), _pct(mt["detection"]["recall"]))
    row("Alert precision", _pct(rt["detection"]["precision"]), _pct(mt["detection"]["precision"]))
    row("Feed failures called access gaps", _pct(ra["reporting_failures_called_access_gaps"]), _pct(ma["reporting_failures_called_access_gaps"]))
    for a, b in zip(ra["per_class"], ma["per_class"]):
        name = SHORT[a["category"]]
        row(f"  {name}: recall / precision", f"{_pct(a['recall'])} / {_pct(a['precision'])}", f"{_pct(b['recall'])} / {_pct(b['precision'])}")
    L.append("")
    L.append("Confusion (true class → reported), columns: access / reporting / uncertain / expected / not flagged")
    for label, cm in (("Rules", ra["confusion"]), ("ML   ", ma["confusion"])):
        for rowname, vals in zip(cm["rows"], cm["matrix"]):
            if rowname in ALERTABLE:
                L.append(f"  {label} {rowname:<18}" + " ".join(f"{v:>4}" for v in vals))
    L.append("")
    L.append(f"Paired, per episode ({paired['episodes']}): both right {paired['both_correct']}, only rules right {paired['rules_only_correct']}, "
             f"only ML right {paired['ml_only_correct']}, both wrong {paired['both_wrong']}; exact McNemar p = {paired['mcnemar_exact_p']}")
    L.append("")
    L.append("Most important features (permutation importance on the test period; built-in importance from training)")
    for imp in r["importances"][:8]:
        L.append(f"  {imp['feature']:<24} permutation {imp['permutation_mean']:+.3f} ± {imp['permutation_std']:.3f}   built-in {imp['impurity']:.3f}")
    s = r["sensitivity"]
    L.append("")
    L.append(f"Sensitivity (test episodes right): other seeds {s['other_seeds']['correct']}, gradient boosting {s['gradient_boosting']['correct']}, "
             f"majority class {s['majority_class']['correct']}, shuffled-label control {s['shuffled_label_control']['correct']} / {r['test']['episodes']}")
    L.append("")
    L.append(f"Where they disagree ({len(r['disagreements'])}):")
    for d in r["disagreements"]:
        why = ", ".join(f"{f['feature']}={f['value']:g}" for f in d["ml_top_features"]) or "no single feature dominates"
        L.append(f"  {d['case_id']}: truth {SHORT[d['truth_category']]}; rules said {SHORT[d['rule_category']]} ({d['rule_reason_code']}), "
                 f"ML said {SHORT[d['ml_category']]} [{why}] → right: {d['correct']}")
    L.append("")
    L.append("Verdict: " + r["conclusion"]["headline"])
    for p in r["conclusion"]["points"]:
        L.append("  - " + p)
    L.append(f"Production classifier: {r['production_classifier']['name']}.")
    L.append(f"results_sha256     {r['results_sha256']}")
    L.append(f"predictions_sha256 {r['predictions_sha256']}")
    return "\n".join(L)


__all__ = ["evaluate_ml", "build_training_set", "format_comparison", "results_hash", "mcnemar_exact_p",
           "InsufficientTrainingData", "MLUnavailable"]
