"""Optional machine-learning triage model (scikit-learn), kept strictly apart from the core engine.

What this module is
  A second opinion on the *category* of an episode the detector has already flagged. It consumes the
  same per-episode evidence the rule-based triage computes (``triage.triage_episode`` output) and
  learns a mapping to the four triage categories. It never decides what to flag, and the dashboard
  keeps the rule-based triage as the default.

What this module is not
  * It is LABEL-FREE: nothing here reads ground-truth labels or imports the evaluation harness.
    Training targets are passed in by the caller (``ml_evaluation`` builds them for the synthetic
    benchmark). ``tests/test_leakage.py`` enforces this.
  * It is optional. numpy and scikit-learn are imported lazily, inside functions, so the standard
    library + Flask installation (and every core test) works without them. PyPI publishes no s390x
    wheels for numpy/scikit-learn; on LinuxONE use the Python of the LinuxONE ML container image,
    which already ships them (see docs/IBM_Z_DEPLOYMENT.md).
"""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

# ----------------------------------------------------------------------------
# Availability (optional dependency)
# ----------------------------------------------------------------------------

# Packages reported for provenance (None = not installed). Only the first two are required.
REQUIRED_PACKAGES = ("scikit-learn", "numpy")
REPORTED_PACKAGES = ("scikit-learn", "numpy", "scipy", "joblib", "threadpoolctl", "pandas", "xgboost", "torch", "tensorflow")

_probe_cache: Optional[dict] = None


class MLUnavailable(RuntimeError):
    """Raised when numpy / scikit-learn cannot be imported in this Python environment."""


def package_versions(names: Sequence[str] = REPORTED_PACKAGES) -> Dict[str, Optional[str]]:
    """Installed versions read from package metadata (does not import the packages)."""
    out: Dict[str, Optional[str]] = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def _probe() -> dict:
    try:
        import numpy  # noqa: F401
        import sklearn  # noqa: F401
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier  # noqa: F401
        from sklearn.inspection import permutation_importance  # noqa: F401
        from sklearn.model_selection import StratifiedKFold  # noqa: F401
    except Exception as exc:  # ImportError, or a broken binary wheel (OSError) on an unusual platform
        return {"available": False,
                "reason": (f"scikit-learn and numpy cannot be imported in this Python environment "
                           f"({type(exc).__name__}: {exc}). The ML triage benchmark is optional; the rule-based triage, "
                           "API and dashboard are unaffected. On LinuxONE (s390x) PyPI has no numpy/scikit-learn wheels: "
                           "run it with the Python of the LinuxONE ML container image, which ships both "
                           "(see docs/IBM_Z_DEPLOYMENT.md). Elsewhere: pip install -r requirements-ml.txt.")}
    return {"available": True, "reason": None}


def availability(refresh: bool = False) -> dict:
    """``{'available', 'reason', 'packages'}``. Imports numpy/scikit-learn once to prove they work."""
    global _probe_cache
    if _probe_cache is None or refresh:
        _probe_cache = _probe()
    return {**_probe_cache, "packages": package_versions()}


def require() -> None:
    status = availability()
    if not status["available"]:
        raise MLUnavailable(status["reason"])


# ----------------------------------------------------------------------------
# Features (pure Python; they read only the rule-free evidence fields of a case record)
# ----------------------------------------------------------------------------

FEATURES: Tuple[Tuple[str, str], ...] = (
    ("shortfall_frac", "Trusted shortfall: 1 − recorded/expected over days with trustworthy counts (0 if there are none)"),
    ("z_agg", "Aggregate robust z-score of the trusted days (0 if there are none)"),
    ("has_trusted_days", "1 if at least one day in the episode has a trustworthy count, else 0"),
    ("feed_problem_share", "Share of open days with a missing or suspect count"),
    ("missing_share", "Share of open days with no count at all"),
    ("low_share", "Share of open days flagged low by the detector"),
    ("flag_flatline_share", "Share of open days inside a statistically implausible flatline run"),
    ("flag_zero_fill_share", "Share of open days with a placeholder zero (zero reported while the feed was down)"),
    ("flag_heartbeat_share", "Share of open days without a feed heartbeat"),
    ("flag_stale_share", "Share of open days where the upstream source had not been seen for over a day"),
    ("flag_incomplete_share", "Share of open days with incomplete upload batches"),
    ("severe_weather", "1 if severe weather coincides with the episode window"),
    ("transport_disruption", "1 if a transport disruption coincides with the episode window"),
    ("reported_outage", "1 if a reported outage coincides with the episode window"),
    ("peer_declining", "Number of comparable services (same site or region) also declining over the same days"),
    ("peers_compared", "Number of comparable services that could be compared"),
    ("history_median", "Median number of comparable past days behind the baseline"),
    ("mean_expected", "Mean expected daily volume (service size)"),
    ("duration_days", "Episode length in days, onset to end"),
)
FEATURE_NAMES: Tuple[str, ...] = tuple(name for name, _ in FEATURES)
FEATURE_DESCRIPTIONS: Dict[str, str] = dict(FEATURES)

# Case-record keys that ``featurize`` is allowed to read. The rule engine's own verdicts
# (category, reason_code, priority, evidence_score, headline, ...) are deliberately absent:
# the model must learn from evidence, not copy the rules' answer. tests/test_ml_triage.py checks this.
CASE_KEYS_USED = frozenset({
    "shortfall_frac", "z_agg", "open_days", "status_counts", "flag_days", "context", "peers",
    "history_median", "mean_expected", "duration_days",
})


def _share(count: float, n_open: int) -> float:
    return max(0.0, min(1.0, count / n_open))


def featurize(case: Mapping) -> List[float]:
    """Numeric feature vector (order = ``FEATURE_NAMES``) from a triage case record."""
    n_open = max(1, int(case["open_days"]))
    counts = case["status_counts"]
    flags = case["flag_days"]
    ctx = case["context"]
    peers = case["peers"]
    frac, z = case["shortfall_frac"], case["z_agg"]
    measured = frac is not None and z is not None
    values = {
        "shortfall_frac": float(frac) if measured else 0.0,
        "z_agg": float(z) if measured else 0.0,
        "has_trusted_days": 1.0 if measured else 0.0,
        "feed_problem_share": _share(counts.get("missing", 0) + counts.get("suspect", 0), n_open),
        "missing_share": _share(counts.get("missing", 0), n_open),
        "low_share": _share(counts.get("low", 0), n_open),
        "flag_flatline_share": _share(flags.get("flatline", 0), n_open),
        "flag_zero_fill_share": _share(flags.get("zero_fill_suspected", 0), n_open),
        "flag_heartbeat_share": _share(flags.get("heartbeat_missing", 0), n_open),
        "flag_stale_share": _share(flags.get("stale_feed", 0), n_open),
        "flag_incomplete_share": _share(flags.get("incomplete_batches", 0), n_open),
        "severe_weather": 1.0 if ctx.get("severe_weather_days", 0) > 0 else 0.0,
        "transport_disruption": 1.0 if ctx.get("transport_days", 0) > 0 else 0.0,
        "reported_outage": 1.0 if ctx.get("outage_days", 0) > 0 else 0.0,
        "peer_declining": float(peers.get("declining", 0)),
        "peers_compared": float(peers.get("compared", 0)),
        "history_median": float(case["history_median"] or 0.0),
        "mean_expected": float(case["mean_expected"] or 0.0),
        "duration_days": float(case["duration_days"]),
    }
    return [values[name] for name in FEATURE_NAMES]


# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSpec:
    """Fixed hyper-parameters, chosen before looking at any test-period result and not tuned afterwards."""
    kind: str = "random_forest"        # or "gradient_boosting" (reported as a sensitivity check)
    n_estimators: int = 300
    max_depth: int = 4
    min_samples_leaf: int = 2
    learning_rate: float = 0.1         # gradient boosting only
    seed: int = 42

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.kind == "random_forest":
            d.pop("learning_rate")
            d["class_weight"] = "balanced"
        else:
            d["max_depth"] = min(self.max_depth, 2)
            d["n_estimators"] = min(self.n_estimators, 100)
        return d


def _make_estimator(spec: ModelSpec):
    require()
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    if spec.kind == "random_forest":
        return RandomForestClassifier(n_estimators=spec.n_estimators, max_depth=spec.max_depth,
                                      min_samples_leaf=spec.min_samples_leaf, class_weight="balanced",
                                      random_state=spec.seed, n_jobs=1)
    if spec.kind == "gradient_boosting":
        return GradientBoostingClassifier(n_estimators=min(spec.n_estimators, 100), max_depth=min(spec.max_depth, 2),
                                          learning_rate=spec.learning_rate, min_samples_leaf=spec.min_samples_leaf,
                                          random_state=spec.seed)
    raise ValueError(f"unknown model kind: {spec.kind}")


class TriageModel:
    """Thin wrapper around a scikit-learn classifier with plain-Python inputs and outputs."""

    def __init__(self, spec: ModelSpec = ModelSpec()):
        self.spec = spec
        self.estimator = None
        self.classes: Tuple[str, ...] = ()
        self._reference: List[float] = []

    def fit(self, rows: Sequence[Sequence[float]], targets: Sequence[str]) -> "TriageModel":
        import numpy as np
        if len(rows) < 2 or len(rows) != len(targets):
            raise ValueError("need at least two training rows and one target per row")
        self.estimator = _make_estimator(self.spec)
        X = np.asarray(rows, dtype=float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.estimator.fit(X, list(targets))
        self.classes = tuple(str(c) for c in self.estimator.classes_)
        self._reference = [float(v) for v in np.median(X, axis=0)]
        return self

    def predict(self, rows: Sequence[Sequence[float]]) -> List[str]:
        import numpy as np
        if not len(rows):
            return []
        return [str(c) for c in self.estimator.predict(np.asarray(rows, dtype=float))]

    def predict_proba(self, rows: Sequence[Sequence[float]]) -> List[Dict[str, float]]:
        import numpy as np
        if not len(rows):
            return []
        probs = self.estimator.predict_proba(np.asarray(rows, dtype=float))
        return [{c: float(p) for c, p in zip(self.classes, row)} for row in probs]

    def impurity_importances(self) -> Dict[str, float]:
        """Built-in importances of the fitted forest (mean decrease in impurity; they sum to 1)."""
        return {name: float(v) for name, v in zip(FEATURE_NAMES, self.estimator.feature_importances_)}

    def permutation_importances(self, rows: Sequence[Sequence[float]], targets: Sequence[str],
                                n_repeats: int = 20, seed: int = 42) -> Dict[str, Tuple[float, float]]:
        """Mean and standard deviation of the accuracy lost when one feature is shuffled."""
        import numpy as np
        from sklearn.inspection import permutation_importance
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = permutation_importance(self.estimator, np.asarray(rows, dtype=float), list(targets),
                                         n_repeats=n_repeats, random_state=seed, scoring="accuracy", n_jobs=1)
        return {name: (float(m), float(s)) for name, m, s in zip(FEATURE_NAMES, res.importances_mean, res.importances_std)}

    def explain(self, row: Sequence[float], top: int = 3) -> List[dict]:
        """Occlusion attribution for one episode.

        Each feature is replaced in turn by its training-set median; the drop in the probability of the
        predicted class says how much that feature mattered for *this* prediction. It is a local,
        model-agnostic diagnostic, not a causal statement.
        """
        import numpy as np
        base = np.asarray(row, dtype=float)
        predicted = self.predict([row])[0]
        k = self.classes.index(predicted)
        p0 = float(self.estimator.predict_proba(base.reshape(1, -1))[0][k])
        variants = np.tile(base, (len(FEATURE_NAMES), 1))
        for j in range(len(FEATURE_NAMES)):
            variants[j, j] = self._reference[j]
        p1 = self.estimator.predict_proba(variants)[:, k]
        scored = [{"feature": FEATURE_NAMES[j], "value": round(float(base[j]), 4),
                   "typical": round(self._reference[j], 4), "effect": round(p0 - float(p1[j]), 4)}
                  for j in range(len(FEATURE_NAMES))]
        scored.sort(key=lambda x: (-x["effect"], x["feature"]))
        return [s for s in scored[:top] if s["effect"] > 0]


def cross_validated_accuracy(rows: Sequence[Sequence[float]], targets: Sequence[str], spec: ModelSpec,
                             n_splits: int = 5) -> Optional[List[float]]:
    """Stratified k-fold accuracy on the TRAINING rows only; None when classes are too small to split."""
    import numpy as np
    from sklearn.model_selection import StratifiedKFold
    counts: Dict[str, int] = {}
    for t in targets:
        counts[t] = counts.get(t, 0) + 1
    splits = min(n_splits, min(counts.values())) if counts else 0
    if splits < 2:
        return None
    X = np.asarray(rows, dtype=float)
    y = np.asarray(list(targets))
    folds: List[float] = []
    for train_idx, test_idx in StratifiedKFold(n_splits=splits, shuffle=True, random_state=spec.seed).split(X, y):
        est = _make_estimator(spec)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            est.fit(X[train_idx], y[train_idx])
        folds.append(float((est.predict(X[test_idx]) == y[test_idx]).mean()))
    return folds
