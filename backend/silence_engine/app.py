"""Flask application: REST API + static frontend.

Security posture
  * Write endpoints require ``Authorization: Bearer <SILENCE_API_TOKEN>`` when a
    token is configured. Without a token they only accept loopback clients
    (local use or an SSH tunnel) that also send an ``X-Requested-With`` header.
    Browsers cannot add that header to a cross-site request without a CORS
    preflight, so a malicious page cannot drive the local API (CSRF). If you
    place a reverse proxy in front of the app, you MUST configure a token,
    because proxied requests look local.
  * CORS is off by default. ``SILENCE_CORS_ORIGINS`` lists exact origins (for
    example a separate frontend dev server) that may call the API.
  * Inputs are validated; errors use one JSON shape:
      {"error": {"code": "...", "message": "...", "details": ...}}
  * Request bodies, tokens and uploaded data are never logged.
"""

from __future__ import annotations

import hmac
import logging
import re
import sqlite3
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import ENGINE_VERSION, audit, database, ingest, ml_triage, reporting, runtime_info, service
from .config import Settings, load_settings
from .service import OperationError

log = logging.getLogger("silence_engine")
CASE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
STREAM_ID_RE = re.compile(r"^(ALL|[A-Za-z0-9_.-]{1,40}:[a-z0-9_]{1,40})$")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
CSRF_HEADER = "X-Requested-With"
DEFAULT_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
               "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")


def create_app(settings: Optional[Settings] = None, start_background: bool = True) -> Flask:
    settings = settings or load_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False
    app.extensions["silence_settings"] = settings
    jobs = service.JobRunner()
    app.extensions["silence_jobs"] = jobs

    with service.connection(settings):
        pass  # creates schema if needed

    if start_background and settings.auto_init_demo and not service.has_data(settings):
        log.info("database empty: generating synthetic demo dataset (seed %s)", settings.demo_seed)
        _submit_init(jobs, settings)

    # ------------------------------------------------------------------ helpers

    def conn() -> sqlite3.Connection:
        if "db" not in g:
            g.db = database.connect(settings.db_path)
        return g.db

    @app.teardown_appcontext
    def _close(_exc: Optional[BaseException]) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def run_or_409() -> dict:
        run = database.current_run(conn())
        if not run:
            job = jobs.latest()
            raise OperationError("not_ready", "No analysis is available yet." +
                                 (" A background job is running." if job and job["status"] == "running" else
                                  " Generate demo data or import a CSV."), 409, {"job": job})
        return run

    def actor() -> str:
        return "api-token" if settings.api_token else "local-api"

    def require_write(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if settings.api_token:
                header = request.headers.get("Authorization", "")
                supplied = header[7:] if header.startswith("Bearer ") else ""
                if not supplied or not hmac.compare_digest(supplied.encode(), settings.api_token.encode()):
                    raise OperationError("unauthorized", "A valid API token is required for this action.", 401)
            else:
                if request.remote_addr not in LOOPBACK:
                    raise OperationError("forbidden", "Write actions are only accepted from the local machine unless "
                                                      "SILENCE_API_TOKEN is configured.", 403)
                if not request.headers.get(CSRF_HEADER):
                    raise OperationError("csrf_header_missing", f"Write requests must send an '{CSRF_HEADER}' header "
                                                                "(any value). This blocks cross-site request forgery.", 403)
            return fn(*args, **kwargs)
        return wrapper

    def arg_int(name: str, default: int, lo: int, hi: int) -> int:
        raw = request.args.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            raise OperationError("invalid_parameter", f"'{name}' must be an integer")
        if not lo <= value <= hi:
            raise OperationError("invalid_parameter", f"'{name}' must be between {lo} and {hi}")
        return value

    def arg_date(name: str) -> Optional[str]:
        raw = request.args.get(name)
        if not raw:
            return None
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            raise OperationError("invalid_parameter", f"'{name}' must be a date (YYYY-MM-DD)")

    def arg_bool(name: str) -> Optional[bool]:
        raw = request.args.get(name)
        if raw is None or raw == "":
            return None
        if raw.lower() in ("1", "true", "yes"):
            return True
        if raw.lower() in ("0", "false", "no"):
            return False
        raise OperationError("invalid_parameter", f"'{name}' must be true or false")

    def json_body() -> dict:
        data = request.get_json(silent=True)
        if data is None:
            if request.data:
                raise OperationError("invalid_json", "Request body must be valid JSON")
            return {}
        if not isinstance(data, dict):
            raise OperationError("invalid_json", "Request body must be a JSON object")
        return data

    # ------------------------------------------------------------------ errors

    @app.errorhandler(OperationError)
    def _op_error(exc: OperationError) -> Response:
        body = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details is not None:
            body["error"]["details"] = exc.details
        return jsonify(body), exc.status

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException) -> Response:
        if not request.path.startswith("/api/"):
            return exc
        code = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}.get(exc.code, "http_error")
        return jsonify({"error": {"code": code, "message": exc.description}}), exc.code

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception) -> Response:
        log.exception("unhandled error on %s %s", request.method, request.path)
        return jsonify({"error": {"code": "internal_error", "message": "Unexpected server error."}}), 500

    def cors_origin() -> Optional[str]:
        origin = (request.headers.get("Origin") or "").rstrip("/")
        return origin if origin and origin in settings.cors_origins and request.path.startswith("/api/") else None

    @app.before_request
    def _preflight() -> Optional[Response]:
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            resp = Response(status=204 if cors_origin() else 403)
            return resp
        return None

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy", settings.csp or DEFAULT_CSP)
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        origin = cors_origin()
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = f"Content-Type, Authorization, {CSRF_HEADER}"
            resp.headers["Access-Control-Max-Age"] = "600"
            resp.headers.add("Vary", "Origin")
        return resp

    # ------------------------------------------------------------------ status

    @app.get("/api/health")
    def health() -> Response:
        db_ok, data_loaded, run, ev, mode = True, False, None, None, None
        try:
            c = conn()
            c.execute("SELECT 1").fetchone()
            data_loaded = c.execute("SELECT 1 FROM observations LIMIT 1").fetchone() is not None
            run = database.current_run(c)
            ev = c.execute("SELECT eval_id, created_at FROM evaluation_results WHERE is_current = 1").fetchone()
            mode = database.get_meta(c, "data_mode")
        except sqlite3.Error:
            db_ok = False
        job = jobs.latest()
        return jsonify({
            "status": "ok" if db_ok else "degraded", "engine_version": ENGINE_VERSION,
            "database": "ok" if db_ok else "error", "data_loaded": data_loaded, "data_mode": mode,
            "synthetic_active": mode in ("synthetic", "mixed"),
            "analysis_ready": bool(run), "analysis_finished_at": run["finished_at"] if run else None,
            "as_of": run["end_date"] if run else None,
            "evaluation_ready": bool(ev), "job": job,
            "runtime": runtime_info.brief(),
        })

    @app.get("/api/system-info")
    def system_info() -> Response:
        c = conn()
        run = database.current_run(c)
        return jsonify({
            "runtime": runtime_info.collect(settings.deployment_label),
            "database": database.db_status(c, settings.db_path),
            "data": {
                "mode": database.get_meta(c, "data_mode"), "demo_seed": database.get_meta(c, "demo_seed"),
                "dataset_fingerprint": database.get_meta(c, "dataset_fingerprint"),
                "synthetic_manifest": database.get_meta_json(c, "synthetic_manifest"),
                "observations": database.observation_stats(c), "recent_batches": database.recent_batches(c),
                "labels_file_present": settings.labels_path.is_file(),
            },
            "analysis": {"current": run, "history": database.run_history(c)},
            "detection_params": {"values": service.current_params(c).to_dict(),
                                 "source": database.get_meta(c, "detection_params_source") or "defaults"},
            "audit": audit.verify(c),
            "config": settings.describe(),
            "write_access": {"token_required": bool(settings.api_token),
                             "client_is_loopback": request.remote_addr in LOOPBACK,
                             "csrf_header": CSRF_HEADER},
            "job": jobs.latest(),
        })

    @app.get("/api/schema")
    def schema() -> Response:
        return jsonify({"fields": ingest.SCHEMA_FIELDS, "required": ingest.REQUIRED_FIELDS,
                        "notes": ["Blank observed_count means NOT REPORTED (missing), which is different from 0.",
                                  "Rows with an invalid site_id, service_type or service_date are rejected.",
                                  "Malformed counts are kept as 'malformed' so data-quality checks can report them.",
                                  "Aggregate data only: do not include personal identifiers."]})

    # ------------------------------------------------------------------ read API

    @app.get("/api/sites")
    def sites() -> Response:
        run = run_or_409()
        return jsonify({"streams": reporting.streams(conn(), run, service.db_key(settings))})

    @app.get("/api/summary")
    def summary() -> Response:
        run = run_or_409()
        window = arg_int("window", 28, 7, 120)
        return jsonify(reporting.summary(conn(), run, service.db_key(settings), window))

    @app.get("/api/cases")
    def cases() -> Response:
        run_or_409()
        cats_raw = request.args.get("category")
        cats = None
        if cats_raw:
            cats = [c.strip().upper() for c in cats_raw.split(",") if c.strip()]
            bad = [c for c in cats if c not in reporting.CATEGORY_LABELS]
            if bad:
                raise OperationError("invalid_parameter", f"unknown category: {', '.join(bad)}")
        feed = request.args.get("feed_status")
        if feed and feed not in ("HEALTHY", "DEGRADED", "FAILED"):
            raise OperationError("invalid_parameter", "feed_status must be HEALTHY, DEGRADED or FAILED")
        site_id = request.args.get("site_id") or None
        if site_id and not ingest.SITE_ID_RE.fullmatch(site_id):
            raise OperationError("invalid_parameter", "invalid site_id")
        sort = request.args.get("sort", "priority")
        if sort not in reporting.SORT_KEYS:
            raise OperationError("invalid_parameter", f"sort must be one of {', '.join(reporting.SORT_KEYS)}")
        review = request.args.get("review") or None
        if review and review not in ("open", "reviewed"):
            raise OperationError("invalid_parameter", "review must be 'open' or 'reviewed'")
        return jsonify(reporting.list_cases(
            conn(), categories=cats, include_expected=bool(arg_bool("include_expected")), site_id=site_id,
            feed_status=feed, ongoing=arg_bool("ongoing"), since=arg_date("since"), review=review, sort=sort,
            descending=request.args.get("order", "desc") != "asc", limit=arg_int("limit", 100, 1, 500),
            offset=arg_int("offset", 0, 0, 100_000)))

    @app.get("/api/cases/<case_id>")
    def case_detail(case_id: str) -> Response:
        if not CASE_ID_RE.fullmatch(case_id):
            raise OperationError("invalid_parameter", "invalid case id")
        run = run_or_409()
        detail = reporting.case_detail(conn(), run, service.db_key(settings), case_id)
        if not detail:
            raise OperationError("not_found", f"Case {case_id} not found", 404)
        return jsonify(detail)

    @app.post("/api/cases/<case_id>/review")
    @require_write
    def case_review(case_id: str) -> Response:
        if not CASE_ID_RE.fullmatch(case_id):
            raise OperationError("invalid_parameter", "invalid case id")
        body = json_body()
        status = str(body.get("status", ""))
        note = body.get("note")
        role = body.get("reviewer_role")
        if note is not None and (not isinstance(note, str) or len(note) > 1000):
            raise OperationError("invalid_parameter", "note must be a string of at most 1000 characters")
        if role is not None and (not isinstance(role, str) or len(role) > 60):
            raise OperationError("invalid_parameter", "reviewer_role must be a string of at most 60 characters")
        review = service.review_case(settings, case_id, status, (note or "").strip() or None,
                                     (role or "").strip() or None, actor())
        return jsonify({"review": review}), 201

    @app.get("/api/timeseries")
    def timeseries() -> Response:
        run = run_or_409()
        stream_id = request.args.get("stream_id", "ALL")
        if not STREAM_ID_RE.fullmatch(stream_id):
            raise OperationError("invalid_parameter", "stream_id must be 'ALL' or '<site_id>:<service_type>'")
        data = reporting.timeseries(conn(), run, service.db_key(settings), stream_id, arg_date("start"), arg_date("end"))
        if data is None:
            raise OperationError("not_found", f"Stream {stream_id} not found", 404)
        return jsonify(data)

    @app.get("/api/silence-map")
    def silence_map() -> Response:
        run = run_or_409()
        return jsonify(reporting.silence_map(conn(), run, service.db_key(settings), arg_date("start"), arg_date("end")))

    @app.get("/api/data-quality")
    def data_quality() -> Response:
        run = run_or_409()
        return jsonify(reporting.data_quality(conn(), run, service.db_key(settings), arg_int("days", 90, 14, 400)))

    @app.get("/api/evaluation")
    def evaluation_get() -> Response:
        c = conn()
        report = database.current_evaluation(c)
        job = jobs.latest()
        if not report:
            return jsonify({"available": False, "job": job,
                            "reason": "No evaluation has been run for the loaded data. Evaluation needs the synthetic "
                                      "dataset's ground-truth labels."})
        mode = database.get_meta(c, "data_mode")
        return jsonify({"available": True, "report": report, "job": job,
                        "stale": report.get("dataset_fingerprint") != (database.get_meta_json(c, "synthetic_manifest") or {}).get("fingerprint_sha256"),
                        "data_mode": mode})

    @app.get("/api/ml-triage")
    def ml_triage_get() -> Response:
        """Optional ML triage benchmark. Returns the comparison only when numpy/scikit-learn are usable here."""
        job = jobs.latest()
        reason = service.ml_unavailable_reason(settings)
        packages = ml_triage.package_versions()
        if reason:
            return jsonify({"available": False, "reason": reason, "packages": packages, "job": job})
        c = conn()
        report = service.current_ml_report(c)
        if not report:
            return jsonify({"available": True, "report": None, "packages": packages, "job": job,
                            "reason": "The benchmark has not been run for the loaded data yet. It needs the synthetic "
                                      "dataset's ground-truth labels."})
        return jsonify({"available": True, "report": report, "packages": packages, "job": job,
                        "stale": service.ml_report_is_stale(c, report)})

    @app.get("/api/audit")
    def audit_list() -> Response:
        c = conn()
        return jsonify({"total": audit.count(c),
                        "entries": audit.list_entries(c, arg_int("limit", 50, 1, 500), arg_int("offset", 0, 0, 10**7))})

    @app.get("/api/audit/verify")
    def audit_verify() -> Response:
        return jsonify(audit.verify(conn()))

    @app.get("/api/jobs/<job_id>")
    def job_get(job_id: str) -> Response:
        job = jobs.get(job_id)
        if not job:
            raise OperationError("not_found", "job not found", 404)
        return jsonify(job)

    @app.get("/api/export/observations.csv")
    def export_csv() -> Response:
        c = conn()
        text = ingest.observations_to_csv(database.load_observations(c), database.load_sites(c))
        return Response(text, mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=silence_engine_observations.csv"})

    # ------------------------------------------------------------------ write API

    @app.post("/api/generate-demo-data")
    @require_write
    def generate_demo() -> Response:
        body = json_body()
        seed = body.get("seed", settings.demo_seed)
        if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**31 - 1:
            raise OperationError("invalid_parameter", "seed must be an integer between 0 and 2147483647")
        who = actor()
        job = jobs.submit("generate-demo-data", lambda p: service.generate_demo(settings, seed, who, p))
        return jsonify({"job": job}), 202

    @app.post("/api/evaluation/run")
    @require_write
    def evaluation_run() -> Response:
        who = actor()

        def work(p: Callable[[str], None]) -> dict:
            result = service.run_evaluation(settings, who, p)
            result["analysis"] = service.run_analysis(settings, who, p)
            result["ml_triage"] = service.refresh_ml_triage(settings, who, p)
            return result
        return jsonify({"job": jobs.submit("evaluation", work)}), 202

    @app.post("/api/ml-triage/run")
    @require_write
    def ml_triage_run() -> Response:
        reason = service.ml_unavailable_reason(settings)
        if reason:
            raise OperationError("ml_unavailable", reason, 409)
        who = actor()
        return jsonify({"job": jobs.submit("ml-triage", lambda p: service.run_ml_evaluation(settings, who, p))}), 202

    @app.post("/api/analysis/run")
    @require_write
    def analysis_run() -> Response:
        return jsonify(service.run_analysis(settings, actor()))

    @app.post("/api/import-csv")
    @require_write
    def import_csv() -> Response:
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise OperationError("missing_file", "Upload a CSV file in the 'file' form field.")
        name = Path(upload.filename).name[:100]
        if not name.lower().endswith(".csv"):
            raise OperationError("invalid_file", "Only .csv files are accepted.")
        mode = request.form.get("mode", "append")
        raw = upload.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise OperationError("invalid_file", "CSV must be UTF-8 encoded.")
        result = ingest.parse_csv_text(text, source=f"csv:{name}", max_rows=settings.max_import_rows)
        if any(i.row == 0 and i.severity == "error" for i in result.issues):
            raise OperationError("validation_failed", "CSV header is invalid.", 422, result.summary())
        out = service.import_records(settings, result, f"csv:{name}", mode, actor())
        return jsonify(out), 201

    @app.post("/api/ingest")
    @require_write
    def ingest_records() -> Response:
        body = json_body()
        records = body.get("records")
        if not isinstance(records, list) or not records:
            raise OperationError("invalid_parameter", "'records' must be a non-empty list")
        if len(records) > 10_000:
            raise OperationError("payload_too_large", "At most 10,000 records per request; use CSV import for more.", 413)
        result = ingest.validate_records(records, source="api", max_rows=10_000)
        out = service.import_records(settings, result, "api", "append", actor(),
                                     reanalyze=body.get("reanalyze", True) is not False)
        return jsonify(out), 201

    # ------------------------------------------------------------------ frontend

    dist = settings.frontend_dist

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def frontend(path: str) -> Response:
        if path.startswith("api/"):
            raise OperationError("not_found", "Unknown API endpoint", 404)
        index = dist / "index.html"
        if not index.is_file():
            return Response(_NOT_BUILT, mimetype="text/html")
        target = (dist / path).resolve()
        if path and target.is_file() and dist.resolve() in target.parents:
            resp = send_from_directory(dist, path)
            if path.startswith("assets/"):  # content-hashed by Vite
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp
        resp = send_from_directory(dist, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    return app


def _submit_init(jobs: service.JobRunner, settings: Settings) -> None:
    def work(progress: Callable[[str], None]) -> Any:
        lock_path = settings.data_dir / ".init.lock"
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")
        try:
            if service.fcntl is not None:
                try:
                    service.fcntl.flock(fh.fileno(), service.fcntl.LOCK_EX | service.fcntl.LOCK_NB)
                except OSError:
                    return {"skipped": "another process is initialising"}
            if service.has_data(settings):
                return {"skipped": "data already present"}
            return service.generate_demo(settings, settings.demo_seed, "system", progress)
        finally:
            fh.close()
    jobs.submit("initial-demo-data", work)


_NOT_BUILT = """<!doctype html><html><head><meta charset="utf-8"><title>SILENCE ENGINE API</title></head>
<body style="font-family:system-ui;background:#0f1216;color:#e8e4da;padding:40px;max-width:680px">
<h1>SILENCE ENGINE — API is running</h1>
<p>The dashboard has not been built. Build it with <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>,
or copy a prebuilt <code>frontend/dist</code> directory next to the backend.</p>
<p>Try <a style="color:#f2a23a" href="/api/health">/api/health</a> or
<a style="color:#f2a23a" href="/api/system-info">/api/system-info</a>.</p></body></html>"""
