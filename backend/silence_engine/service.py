"""Write-side operations shared by the API and the CLI.

Each operation opens its own SQLite connection, holds an exclusive file lock
for writes (safe across threads and across gunicorn worker processes on
Linux/macOS), and appends to the audit chain.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import logging
import threading
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from . import (ENGINE_VERSION, audit, data_generator, database, evaluation, ingest, ml_evaluation, ml_triage, pipeline,
               runtime_info)
from .config import Settings
from .detection import DetectionParams
from .ingest import Observation

log = logging.getLogger("silence_engine")

try:  # POSIX advisory locks (Linux incl. s390x, macOS)
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore

_thread_lock = threading.RLock()
ML_REPORT_KEY = "ml_triage_report"


class OperationError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


@contextlib.contextmanager
def write_lock(settings: Settings) -> Iterator[None]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        if fcntl is None:
            yield
            return
        with open(settings.data_dir / ".write.lock", "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def connection(settings: Settings) -> Iterator[Any]:
    conn = database.connect(settings.db_path)
    try:
        database.init_schema(conn)
        yield conn
    finally:
        conn.close()


def db_key(settings: Settings) -> str:
    return str(settings.db_path)


def dataset_fingerprint(observations: List[Observation]) -> str:
    rows = sorted(observations, key=lambda o: (o.site_id, o.service_type, o.service_date.isoformat(),
                                               o.report_received_at.isoformat() if o.report_received_at else "",
                                               str(o.observed_count), o.value_status))
    return hashlib.sha256(ingest.observations_to_csv(rows).encode("utf-8")).hexdigest()


def has_data(settings: Settings) -> bool:
    with connection(settings) as conn:
        return conn.execute("SELECT 1 FROM observations LIMIT 1").fetchone() is not None


def current_params(conn) -> DetectionParams:
    return DetectionParams.from_dict(database.get_meta_json(conn, "detection_params"))


# ----------------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------------

def generate_demo(settings: Settings, seed: int, actor: str = "system",
                  progress: Optional[Callable[[str], None]] = None) -> dict:
    """Regenerate the SYNTHETIC dataset, evaluate it, then analyse with tuned thresholds."""
    say = progress or (lambda _m: None)
    say("generating synthetic dataset")
    ds = data_generator.generate(seed=seed)
    out_dir = settings.data_dir / "synthetic"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_raw_csv(out_dir / "observations.csv", ds.raw_records)
    (out_dir / "manifest.json").write_text(json.dumps(ds.manifest, indent=2), encoding="utf-8")
    settings.labels_path.parent.mkdir(parents=True, exist_ok=True)
    settings.labels_path.write_text(json.dumps(ds.labels, indent=1), encoding="utf-8")

    vr = ingest.validate_records(ds.raw_records, source="synthetic")
    batch_id = database.new_id("batch")
    with write_lock(settings), connection(settings) as conn:
        database.clear_data(conn)
        database.upsert_sites(conn, ds.sites, synthetic=True)
        database.insert_observations(conn, vr.observations, batch_id)
        database.record_batch(conn, batch_id, "synthetic", "replace", len(vr.observations), vr.rejected, vr.summary(10))
        fp = dataset_fingerprint(vr.observations)
        database.set_meta(conn, "data_mode", "synthetic")
        database.set_meta(conn, "demo_seed", str(seed))
        database.set_meta(conn, "dataset_fingerprint", fp)
        database.set_meta_json(conn, "synthetic_manifest", ds.manifest)
        database.delete_meta(conn, "detection_params")
        database.delete_meta(conn, "detection_params_source")
        database.delete_meta(conn, ML_REPORT_KEY)
        audit.append(conn, "demo_data_generated", {"seed": seed, "records": len(vr.observations),
                                                    "raw_fingerprint": ds.manifest["fingerprint_sha256"],
                                                    "dataset_fingerprint": fp,
                                                    "generator_version": ds.manifest["generator_version"]},
                     actor=actor, entity_type="dataset", entity_id=fp[:16])
        conn.commit()
    eval_summary = run_evaluation(settings, actor=actor, progress=progress)
    analysis = run_analysis(settings, actor=actor, progress=progress)
    ml = refresh_ml_triage(settings, actor=actor, progress=progress)
    return {"seed": seed, "records": len(vr.observations), "manifest": ds.manifest,
            "evaluation": eval_summary, "analysis": analysis, "ml_triage": ml}


def _write_raw_csv(path: Path, records: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ingest.SCHEMA_FIELD_NAMES, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for r in records:
            row = dict(r)
            for key in ("scheduled_open", "feed_heartbeat_received", "transport_disruption", "reported_outage"):
                if isinstance(row.get(key), bool):
                    row[key] = int(row[key])
            if row.get("observed_count") is None:
                row["observed_count"] = ""
            writer.writerow(row)


def run_analysis(settings: Settings, actor: str = "system", progress: Optional[Callable[[str], None]] = None) -> dict:
    say = progress or (lambda _m: None)
    say("running detection and triage")
    started = database.utcnow()
    t0 = time.time()
    with write_lock(settings), connection(settings) as conn:
        observations = database.load_observations(conn)
        if not observations:
            raise OperationError("no_data", "No observations loaded. Generate demo data or import a CSV first.", 409)
        sites = database.load_sites(conn)
        params = current_params(conn)
        result = pipeline.analyze(observations, sites, params)
        day_index = pipeline.case_day_index(result)
        rows = []
        for sid, days in result.assessments.items():
            sd = result.streams[sid]
            cmap = day_index.get(sid, {})
            for a in days:
                rows.append((None, sid, sd.site_id, sd.service_type, a.date.isoformat(), a.observed, a.value_status,
                             a.status, int(a.scheduled_open), int(a.schedule_inferred), a.expected, a.typical_if_open,
                             a.sigma, a.n_history, a.z, a.shortfall, a.shortfall_frac, a.cusum, int(a.trusted),
                             json.dumps(a.flags), a.weather, None if a.transport is None else int(a.transport),
                             None if a.outage is None else int(a.outage), None if a.heartbeat is None else int(a.heartbeat),
                             a.batch_completeness, a.stale_days, cmap.get(a.date)))
        run_id = database.new_id("run")
        rows = [(run_id,) + r[1:] for r in rows]
        dq = pipeline.dq_report(result)
        duration = int(1000 * (time.time() - t0))
        run = {
            "run_id": run_id, "started_at": started, "finished_at": database.utcnow(), "duration_ms": duration,
            "end_date": result.end_date.isoformat() if result.end_date else None,
            "params": {"detection": params.to_dict(), "triage": result.triage_params.to_dict(),
                       "source": database.get_meta(conn, "detection_params_source") or "defaults"},
            "n_observations": len(observations),
            "dataset_fingerprint": database.get_meta(conn, "dataset_fingerprint"),
            "engine_version": ENGINE_VERSION, "platform": runtime_info.brief(), "dq": dq,
        }
        database.save_analysis(conn, run, rows, result.cases)
        counts: Dict[str, int] = {}
        for c in result.cases:
            counts[c["category"]] = counts.get(c["category"], 0) + 1
        audit.append(conn, "analysis_run", {"run_id": run_id, "observations": len(observations),
                                             "cases": len(result.cases), "by_category": counts,
                                             "duration_ms": duration, "params": params.to_dict(),
                                             "platform": runtime_info.brief()},
                     actor=actor, entity_type="analysis_run", entity_id=run_id)
        conn.commit()
    log.info("analysis %s: %d observations, %d cases in %d ms", run_id, len(observations), len(result.cases), duration)
    return {"run_id": run_id, "cases": len(result.cases), "by_category": counts, "duration_ms": duration}


def _labelled_synthetic(settings: Settings) -> tuple:
    """Labels, synthetic observations, sites and manifest, or an OperationError explaining why there is nothing to evaluate."""
    labels = evaluation.load_labels(settings.labels_path)
    if labels is None:
        raise OperationError("no_labels", "No ground-truth label file found. Evaluation is only available for the "
                                          "synthetic demo dataset.", 409)
    with connection(settings) as conn:
        observations = [o for o in database.load_observations(conn) if o.source == "synthetic"]
        sites = database.load_sites(conn)
        manifest = database.get_meta_json(conn, "synthetic_manifest") or {}
    if not observations:
        raise OperationError("no_synthetic_data", "No synthetic observations are loaded, so there is nothing with "
                                                  "ground truth to evaluate.", 409)
    if manifest.get("fingerprint_sha256") != labels.get("dataset_fingerprint_sha256"):
        raise OperationError("labels_mismatch", "The label file does not belong to the loaded synthetic dataset. "
                                                "Regenerate the demo data.", 409)
    return labels, observations, sites, manifest


def run_evaluation(settings: Settings, actor: str = "system", progress: Optional[Callable[[str], None]] = None) -> dict:
    labels, observations, sites, _manifest = _labelled_synthetic(settings)
    report = evaluation.evaluate(observations, sites, labels, include_replay=settings.eval_replay, progress=progress)
    report["generated_at"] = database.utcnow()
    report["platform"] = runtime_info.brief()
    eval_id = database.new_id("eval")
    with write_lock(settings), connection(settings) as conn:
        database.save_evaluation(conn, eval_id, report)
        database.set_meta_json(conn, "detection_params", report["engine_params_full"])
        database.set_meta(conn, "detection_params_source", f"tuned on synthetic training period ({eval_id})")
        audit.append(conn, "evaluation_run", {"eval_id": eval_id, "results_sha256": report["results_sha256"],
                                               "duration_seconds": report["duration_seconds"],
                                               "tuned_params": report["tuned_params"]["engine"],
                                               "platform": runtime_info.brief()},
                     actor=actor, entity_type="evaluation", entity_id=eval_id)
        conn.commit()
    eng = report["test"]["engine"]
    return {"eval_id": eval_id, "results_sha256": report["results_sha256"],
            "duration_seconds": report["duration_seconds"],
            "test_recall": eng["detection"]["recall"], "test_precision": eng["detection"]["precision"]}


def ml_unavailable_reason(settings: Settings) -> Optional[str]:
    """Why the optional ML triage benchmark cannot run here (None = it can)."""
    if not settings.ml_triage:
        return "The ML triage benchmark is switched off (SILENCE_ML_TRIAGE=0)."
    status = ml_triage.availability()
    return None if status["available"] else status["reason"]


def run_ml_evaluation(settings: Settings, actor: str = "system",
                      progress: Optional[Callable[[str], None]] = None) -> dict:
    """Train the optional ML triage model on the synthetic training period and compare it with the rules.

    Uses the detector thresholds stored by the last evaluation (tuned on the training period). Stores the
    report in the meta table; nothing about the rule-based cases, scores or evaluation changes.
    """
    reason = ml_unavailable_reason(settings)
    if reason:
        raise OperationError("ml_unavailable", reason, 409)
    labels, observations, sites, _manifest = _labelled_synthetic(settings)
    with connection(settings) as conn:
        params = current_params(conn)
    try:
        report = ml_evaluation.evaluate_ml(observations, sites, labels, params, progress=progress)
    except ml_evaluation.InsufficientTrainingData as exc:
        raise OperationError("ml_insufficient_data", str(exc), 409)
    report["generated_at"] = database.utcnow()
    report["platform"] = runtime_info.brief()
    with write_lock(settings), connection(settings) as conn:
        database.set_meta_json(conn, ML_REPORT_KEY, report)
        audit.append(conn, "ml_triage_evaluated", {
            "results_sha256": report["results_sha256"], "predictions_sha256": report["predictions_sha256"],
            "model": report["model"]["spec"], "libraries": report["runtime"]["libraries"],
            "verdict": report["conclusion"]["verdict"], "duration_seconds": report["duration_seconds"],
            "platform": runtime_info.brief()}, actor=actor, entity_type="ml_triage", entity_id=report["results_sha256"][:16])
        conn.commit()
    test = report["test"]
    return {"results_sha256": report["results_sha256"], "predictions_sha256": report["predictions_sha256"],
            "duration_seconds": report["duration_seconds"], "verdict": report["conclusion"]["verdict"],
            "rules_explanation_accuracy": test["rules"]["explanation"]["accuracy_given_detection"],
            "ml_explanation_accuracy": test["ml"]["explanation"]["accuracy_given_detection"]}


def refresh_ml_triage(settings: Settings, actor: str = "system",
                      progress: Optional[Callable[[str], None]] = None) -> dict:
    """Best-effort ML benchmark run after the core pipeline; it can never make the core operation fail."""
    reason = ml_unavailable_reason(settings)
    if reason:
        return {"skipped": reason}
    try:
        return run_ml_evaluation(settings, actor, progress)
    except OperationError as exc:
        return {"skipped": exc.message}
    except Exception:  # pragma: no cover - logged for operators
        log.error("ML triage benchmark failed:\n%s", traceback.format_exc())
        return {"skipped": "The ML triage benchmark failed; see the server log. The rule-based results are unaffected."}


def current_ml_report(conn) -> Optional[dict]:
    return database.get_meta_json(conn, ML_REPORT_KEY)


def ml_report_is_stale(conn, report: dict) -> bool:
    manifest = database.get_meta_json(conn, "synthetic_manifest") or {}
    return (report.get("dataset_fingerprint") != manifest.get("fingerprint_sha256")
            or report.get("detector", {}).get("params") != current_params(conn).to_dict())


def import_records(settings: Settings, result: ingest.ValidationResult, source: str, mode: str, actor: str,
                   reanalyze: bool = True) -> dict:
    if mode not in ("append", "replace"):
        raise OperationError("invalid_mode", "mode must be 'append' or 'replace'")
    summary = result.summary()
    if not result.observations:
        raise OperationError("validation_failed", "No valid rows to import.", 422, summary)
    batch_id = database.new_id("batch")
    with write_lock(settings), connection(settings) as conn:
        if mode == "replace":
            database.clear_data(conn)
            database.delete_meta(conn, "synthetic_manifest")
            database.delete_meta(conn, "detection_params")
            database.delete_meta(conn, "detection_params_source")
            database.delete_meta(conn, ML_REPORT_KEY)
        database.upsert_sites(conn, result.sites, synthetic=False)
        database.insert_observations(conn, result.observations, batch_id)
        database.record_batch(conn, batch_id, source, mode, len(result.observations), result.rejected, summary)
        prior_mode = database.get_meta(conn, "data_mode")
        new_mode = "imported" if mode == "replace" else ("mixed" if prior_mode in ("synthetic", "mixed") else "imported")
        database.set_meta(conn, "data_mode", new_mode)
        fp = dataset_fingerprint(database.load_observations(conn))
        database.set_meta(conn, "dataset_fingerprint", fp)
        audit.append(conn, "records_imported", {"batch_id": batch_id, "source": source, "mode": mode,
                                                 "accepted": len(result.observations), "rejected": result.rejected,
                                                 "malformed_counts": summary["malformed_counts"],
                                                 "dataset_fingerprint": fp},
                     actor=actor, entity_type="ingest_batch", entity_id=batch_id)
        conn.commit()
    out = {"batch_id": batch_id, "mode": mode, "data_mode": new_mode, "validation": summary}
    if reanalyze:
        out["analysis"] = run_analysis(settings, actor=actor)
    return out


def review_case(settings: Settings, case_id: str, status: str, note: Optional[str], reviewer_role: Optional[str],
                actor: str) -> dict:
    from .reporting import REVIEW_STATUSES
    if status not in REVIEW_STATUSES:
        raise OperationError("invalid_status", f"status must be one of {', '.join(REVIEW_STATUSES)}")
    with write_lock(settings), connection(settings) as conn:
        if not database.load_case(conn, case_id):
            raise OperationError("not_found", f"Case {case_id} not found", 404)
        review = database.add_review(conn, case_id, status, note, reviewer_role)
        audit.append(conn, "case_reviewed", {"status": status, "reviewer_role": reviewer_role,
                                              "note_length": len(note or "")},
                     actor=actor, entity_type="case", entity_id=case_id)
        conn.commit()
    return review


# ----------------------------------------------------------------------------
# Background jobs (single worker thread; long operations never block requests)
# ----------------------------------------------------------------------------

class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, dict] = {}
        self._active: Optional[str] = None

    def submit(self, kind: str, fn: Callable[[Callable[[str], None]], Any]) -> dict:
        with self._lock:
            if self._active and self._jobs[self._active]["status"] == "running":
                raise OperationError("job_running", "Another long-running job is in progress.", 409,
                                     {"job": self._jobs[self._active]})
            job_id = database.new_id("job")
            job = {"job_id": job_id, "kind": kind, "status": "running", "message": "queued",
                   "started_at": database.utcnow(), "finished_at": None, "result": None, "error": None}
            self._jobs[job_id] = job
            self._active = job_id

        def progress(msg: str) -> None:
            job["message"] = msg

        def target() -> None:
            try:
                job["result"] = fn(progress)
                job["status"] = "succeeded"
                job["message"] = "done"
            except OperationError as exc:
                job["status"], job["error"] = "failed", {"code": exc.code, "message": exc.message}
            except Exception as exc:  # pragma: no cover - logged for operators
                log.error("job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
                job["status"], job["error"] = "failed", {"code": "internal_error", "message": "Job failed; see server log."}
            finally:
                job["finished_at"] = database.utcnow()

        threading.Thread(target=target, name=f"job-{kind}", daemon=True).start()
        return dict(job)

    def get(self, job_id: str) -> Optional[dict]:
        job = self._jobs.get(job_id)
        return dict(job) if job else None

    def latest(self) -> Optional[dict]:
        return self.get(self._active) if self._active else None
