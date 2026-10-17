"""Guards against ground-truth leakage into the detector."""

import ast
import dataclasses
import json
import random
from pathlib import Path

from silence_engine import data_generator, database, evaluation, ingest, pipeline, service
from silence_engine.detection import DetectionParams

PKG = Path(__file__).resolve().parent.parent / "silence_engine"
DETECTOR_MODULES = ("data_quality.py", "detection.py", "triage.py", "pipeline.py", "ingest.py", "reporting.py", "stats.py")
FORBIDDEN_IMPORTS = {"evaluation", "data_generator", "ml_evaluation", "ml_triage"}
FORBIDDEN_WORDS = ("ground_truth", "load_labels", "event_class", "labels_path", "EVENT_CLASSES")
LABEL_LIKE = {"label", "labels", "event_id", "event_class", "scenario", "truth", "ground_truth", "subtype", "class"}


def test_detector_modules_never_import_labels_or_generator():
    for name in DETECTOR_MODULES:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names} | {(node.module or "").split(".")[-1]}
                assert not names & FORBIDDEN_IMPORTS, f"{name} imports {names & FORBIDDEN_IMPORTS}"
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[-1] for a in node.names} & FORBIDDEN_IMPORTS
        source = (PKG / name).read_text(encoding="utf-8")
        for word in FORBIDDEN_WORDS:
            assert word not in source, f"{name} references {word}"


def test_observation_model_and_table_have_no_label_fields():
    fields = {f.name for f in dataclasses.fields(ingest.Observation)}
    assert not fields & LABEL_LIKE
    assert not set(database.OBS_COLUMNS) & LABEL_LIKE
    assert "label" not in database.SCHEMA.split("CREATE TABLE IF NOT EXISTS observations")[1].split(");")[0]


def test_generator_raw_records_contain_only_schema_fields(demo_dataset):
    allowed = set(ingest.SCHEMA_FIELD_NAMES)
    for r in demo_dataset.raw_records:
        assert set(r) <= allowed


def test_pipeline_output_independent_of_label_file(make_settings):
    settings = make_settings()
    service.generate_demo(settings, seed=7, actor="test")
    with service.connection(settings) as conn:
        before = json.dumps(database.load_cases(conn), sort_keys=True)
    # Corrupt the labels completely: shuffled classes and dates.
    labels = json.loads(settings.labels_path.read_text())
    rng = random.Random(0)
    classes = [e["class"] for e in labels["events"]]
    rng.shuffle(classes)
    for e, c in zip(labels["events"], classes):
        e["class"] = c
    settings.labels_path.write_text(json.dumps(labels))
    service.run_analysis(settings, actor="test")
    with service.connection(settings) as conn:
        after_shuffle = json.dumps(database.load_cases(conn), sort_keys=True)
    settings.labels_path.unlink()
    service.run_analysis(settings, actor="test")
    with service.connection(settings) as conn:
        after_delete = json.dumps(database.load_cases(conn), sort_keys=True)
    assert before == after_shuffle == after_delete


def test_tuning_ignores_test_period_labels(demo_dataset, demo_observations):
    labels = evaluation.normalise_labels(demo_dataset.labels)
    obs = demo_observations
    protocol = evaluation.make_protocol(min(o.service_date for o in obs), max(o.service_date for o in obs))
    preps = {"engine": pipeline.prepare(obs, demo_dataset.sites, params=DetectionParams(use_feed_health=True)),
             "robust": pipeline.prepare(obs, demo_dataset.sites, params=DetectionParams(use_feed_health=False))}
    full, _ = evaluation.tune("engine", preps, obs, labels["events"], protocol)
    train_only = [e for e in labels["events"] if e["_start"] <= protocol.train_end]
    tuned_train_only, _ = evaluation.tune("engine", preps, obs, train_only, protocol)
    assert full == tuned_train_only
    naive_full, _ = evaluation.tune("naive", preps, obs, labels["events"], protocol)
    naive_train, _ = evaluation.tune("naive", preps, obs, train_only, protocol)
    assert naive_full == naive_train


def test_protocol_periods_are_disjoint_and_ordered(demo_observations):
    obs = demo_observations
    p = evaluation.make_protocol(min(o.service_date for o in obs), max(o.service_date for o in obs))
    assert p.start <= p.warmup_end < p.train_start <= p.train_end < p.test_start <= p.test_end


# ----------------------------------------------------------------------------
# Optional ML triage: labels may only be used by evaluation-style code
# ----------------------------------------------------------------------------

ML_ROOT = PKG.parent.parent
LABEL_READING_WORDS = FORBIDDEN_WORDS  # ground_truth, load_labels, event_class, labels_path, EVENT_CLASSES
ML_STACK = {"numpy", "scipy", "sklearn", "pandas", "xgboost", "torch", "tensorflow", "joblib"}


def _imported_modules(tree, include_nested: bool):
    """Top-level names of imported modules; module-level statements only unless include_nested."""
    found = set()
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(((node.module or "") if node.level == 0 else "").split(".")[0] or ("." + (node.module or "")))
            found |= {a.name for a in node.names} if node.level else set()
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not include_nested:
            continue
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                stack.append(child)
    return found


def test_ml_triage_module_is_label_free():
    source = (PKG / "ml_triage.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = _imported_modules(tree, include_nested=True)
    assert not imported & {"evaluation", "data_generator", "ml_evaluation", "database", "service", "app", "pipeline"}
    for word in LABEL_READING_WORDS:
        assert word not in source, f"ml_triage.py references {word}"
    for node in ast.walk(tree):  # relative imports such as "from . import evaluation"
        if isinstance(node, ast.ImportFrom) and node.level:
            names = {a.name for a in node.names} | {(node.module or "")}
            assert not names & {"evaluation", "data_generator", "ml_evaluation"}, names


def test_ml_evaluation_receives_labels_it_never_loads_itself():
    source = (PKG / "ml_evaluation.py").read_text(encoding="utf-8")
    for word in LABEL_READING_WORDS:
        assert word not in source, f"ml_evaluation.py references {word}"
    assert "data_generator" not in _imported_modules(ast.parse(source), include_nested=True)
    assert "open(" not in source and "read_text" not in source  # labels arrive as an argument, never from disk here


def test_detector_modules_never_import_the_ml_modules():
    for name in DETECTOR_MODULES + ("config.py", "database.py", "audit.py", "runtime_info.py"):
        imported = _imported_modules(ast.parse((PKG / name).read_text(encoding="utf-8")), include_nested=True)
        assert not imported & {"ml_triage", "ml_evaluation"}, f"{name} imports an ML module"


def test_ml_stack_is_imported_lazily_and_only_by_the_ml_modules():
    for name in ("ml_triage.py", "ml_evaluation.py"):
        top = _imported_modules(ast.parse((PKG / name).read_text(encoding="utf-8")), include_nested=False)
        assert not top & ML_STACK, f"{name} imports {top & ML_STACK} at module level"
    for path in sorted(PKG.glob("*.py")):
        if path.name in ("ml_triage.py", "ml_evaluation.py"):
            continue
        assert not _imported_modules(ast.parse(path.read_text(encoding="utf-8")), include_nested=True) & ML_STACK, path.name


def test_core_requirements_do_not_pull_in_the_ml_stack():
    for name in ("requirements.txt", "requirements-deploy.txt", "requirements-dev.txt", "requirements-lock.txt"):
        text = (ML_ROOT / name).read_text(encoding="utf-8").lower()
        assert not any(pkg in text for pkg in ("numpy", "scikit-learn", "scipy", "pandas", "xgboost")), name
    optional = (ML_ROOT / "requirements-ml.txt").read_text(encoding="utf-8").lower()
    assert "scikit-learn" in optional and "numpy" in optional


def test_importing_the_package_does_not_import_numpy_or_sklearn():
    import subprocess
    import sys
    code = ("import sys; sys.path.insert(0, %r); import silence_engine.service, silence_engine.app, silence_engine.ml_triage, "
            "silence_engine.ml_evaluation; print(sorted(m for m in ('numpy', 'sklearn', 'scipy') if m in sys.modules))") % str(PKG.parent)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"
