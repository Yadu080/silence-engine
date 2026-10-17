#!/usr/bin/env python3
"""SILENCE ENGINE command-line interface.

  python manage.py init-demo [--seed 42] [--if-empty]   generate SYNTHETIC data, evaluate, analyse
  python manage.py analyze                              re-run detection + triage on stored data
  python manage.py evaluate [--out report.json]         re-run the evaluation (synthetic data only)
  python manage.py evaluate-ml [--out ml_report.json]   optional ML triage vs rules (needs scikit-learn; see requirements-ml.txt)
  python manage.py import-csv FILE [--mode append|replace]
  python manage.py verify-audit                         recompute the audit hash chain
  python manage.py fingerprint                          print reproducibility hashes
  python manage.py check-env                            print runtime facts (architecture, versions)
  python manage.py serve [--host 127.0.0.1] [--port 8000]   development server (use gunicorn in production)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from silence_engine import audit, database, ingest, ml_evaluation, ml_triage, runtime_info, service
from silence_engine.config import load_settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SILENCE ENGINE management commands")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init-demo")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--if-empty", action="store_true", help="only generate when no data is loaded")
    sub.add_parser("analyze")
    p = sub.add_parser("evaluate")
    p.add_argument("--out", type=Path, default=None)
    p = sub.add_parser("evaluate-ml", help="train the optional ML triage model on the synthetic training period and compare it with the rules")
    p.add_argument("--out", type=Path, default=None, help="write the full JSON report here")
    p = sub.add_parser("import-csv")
    p.add_argument("file", type=Path)
    p.add_argument("--mode", choices=("append", "replace"), default="append")
    sub.add_parser("verify-audit")
    sub.add_parser("fingerprint")
    sub.add_parser("check-env")
    p = sub.add_parser("serve")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    settings = load_settings()

    def progress(msg: str) -> None:
        print(f"  … {msg}", flush=True)

    try:
        if args.cmd == "init-demo":
            if args.if_empty and service.has_data(settings):
                print("Data already present; nothing to do (use without --if-empty to regenerate).")
                return 0
            seed = settings.demo_seed if args.seed is None else args.seed
            print(f"Generating SYNTHETIC demo dataset (seed {seed}) into {settings.db_path}")
            out = service.generate_demo(settings, seed, "cli", progress)
            print(json.dumps({"records": out["records"], "fingerprint": out["manifest"]["fingerprint_sha256"],
                              "evaluation": out["evaluation"], "analysis": out["analysis"]}, indent=2))
        elif args.cmd == "analyze":
            print(json.dumps(service.run_analysis(settings, "cli", progress), indent=2))
        elif args.cmd == "evaluate":
            out = service.run_evaluation(settings, "cli", progress)
            print(json.dumps(out, indent=2))
            if args.out:
                with service.connection(settings) as conn:
                    args.out.write_text(json.dumps(database.current_evaluation(conn), indent=2), encoding="utf-8")
                print(f"Full report written to {args.out}")
        elif args.cmd == "evaluate-ml":
            reason = service.ml_unavailable_reason(settings)
            if reason:
                print(f"ML triage is not available here: {reason}", file=sys.stderr)
                return 3
            service.run_ml_evaluation(settings, "cli", progress)
            with service.connection(settings) as conn:
                report = service.current_ml_report(conn)
            print(ml_evaluation.format_comparison(report))
            if args.out:
                args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(f"Full report written to {args.out}")
        elif args.cmd == "import-csv":
            text = args.file.read_text(encoding="utf-8")
            result = ingest.parse_csv_text(text, source=f"csv:{args.file.name}", max_rows=settings.max_import_rows)
            out = service.import_records(settings, result, f"csv:{args.file.name}", args.mode, "cli")
            print(json.dumps(out, indent=2, default=str))
        elif args.cmd == "verify-audit":
            with service.connection(settings) as conn:
                res = audit.verify(conn)
            print(json.dumps(res, indent=2))
            return 0 if res["valid"] else 2
        elif args.cmd == "fingerprint":
            with service.connection(settings) as conn:
                ev = database.current_evaluation(conn) or {}
                manifest = database.get_meta_json(conn, "synthetic_manifest") or {}
                print(json.dumps({
                    "machine": runtime_info.brief(),
                    "raw_synthetic_fingerprint": manifest.get("fingerprint_sha256"),
                    "dataset_fingerprint": database.get_meta(conn, "dataset_fingerprint"),
                    "evaluation_results_sha256": ev.get("results_sha256"),
                    "audit_chain": audit.verify(conn),
                }, indent=2))
        elif args.cmd == "check-env":
            print(json.dumps(runtime_info.collect(settings.deployment_label), indent=2))
        elif args.cmd == "serve":
            from silence_engine.app import create_app
            app = create_app(settings)
            app.run(host=args.host or settings.host, port=args.port or settings.port, debug=False, threaded=True)
    except service.OperationError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        if exc.details:
            print(json.dumps(exc.details, indent=2, default=str), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
