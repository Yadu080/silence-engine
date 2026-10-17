# Architecture

## Where each component runs

```
 ┌────────────────────────┐        HTTPS or SSH tunnel         ┌─────────────────────────────────────────────────────────┐
 │  Analyst's browser     │ ─────────────────────────────────▶ │  LinuxONE (s390x) — operational backend, non-root user   │
 │  React dashboard       │   same origin: /  and  /api/*      │                                                           │
 │  (static files only)   │                                    │  gunicorn (1 worker × 4 threads) ── Flask app (app.py)    │
 └────────────────────────┘                                    │      │                                                    │
                                                               │      ├─ ingest.py        validation (CSV / JSON / synthetic)
 ┌────────────────────────┐   scp release tarball              │      ├─ data_quality.py  feed-health checks              │
 │  Development Mac       │ ─────────────────────────────────▶ │      ├─ detection.py     baselines, z, CUSUM, episodes   │
 │  Node + Vite build     │   (frontend/dist prebuilt,         │      ├─ triage.py        hypotheses, evidence, scores    │
 │  same backend + tests  │    no Node.js on the server)       │      ├─ evaluation.py    held-out evaluation             │
 └────────────────────────┘                                    │      ├─ service.py       locked writes, background jobs  │
                                                               │      └─ database.py ─▶ SQLite (WAL): observations, cases,  │
                                                               │                         reviews, runs, hash-chained audit │
                                                               │         evaluation.py ─▶ separate label file (synthetic   │
                                                               │                         ground truth, evaluation only)    │
                                                               └─────────────────────────────────────────────────────────┘
```

The same Python package runs unchanged on macOS (arm64/x86_64) and Linux on IBM Z (s390x). The Platform page reports which one is actually serving: it reads `platform.machine()` and, on IBM Z, selected fields from `/proc/sysinfo`. An optional `SILENCE_DEPLOYMENT_LABEL` is displayed separately and marked as unverified.

## Data flow

```
raw records ──▶ ingest.validate_records ──▶ observations table (inputs only, no labels)
                     │  (blank → missing, bad → malformed, 0 → zero)
                     ▼
pipeline.prepare:  data_quality.prepare_streams  ──▶ one DayRecord per stream-day + feed flags
                   detection.compute_baselines   ──▶ causal expected value + σ per day
pipeline.analyze_prepared:
                   detection.assess_stream       ──▶ status, z, shortfall, CUSUM per day
                   detection.find_episodes       ──▶ silence / closure episodes, alert dates
                   triage.triage_episode         ──▶ case: category, evidence, hypotheses, scores, steps
service.run_analysis ──▶ daily_assessments + cases tables (current run) + audit entry
reporting.*          ──▶ every API read (summary, queue, map, charts) from those two tables
```

`evaluation.py` calls the same `pipeline` functions with different thresholds and compares predictions to the label file. Detector modules never import the evaluation module, the generator or the ML modules (enforced by an AST test).

```
flagged episodes (rules' evidence) ──▶ ml_triage.featurize ──▶ RandomForest ──▶ category   (optional second opinion)
                                  └──▶ triage._decide      ──▶ category   (the default shown in the dashboard)
ml_evaluation: trains on the synthetic TRAINING period, scores the TEST period with evaluation.score for both
```

## Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `ingest.py` | Public schema, validation, CSV parsing/export | stdlib |
| `data_generator.py` | Reproducible synthetic records + separate labels | stdlib |
| `data_quality.py` | Feed-health flags, trust, issue runs, stream health | `stats`, `ingest` |
| `detection.py` | Baselines, statuses, CUSUM, episodes | `stats`, `data_quality` |
| `triage.py` | Features, decision rules, evidence, scores | `stats`, `detection` |
| `pipeline.py` | Orchestration, pure functions | the four above |
| `evaluation.py` | Protocol, tuning, metrics, robustness, replay | `pipeline` |
| `ml_triage.py` | *Optional.* Features, scikit-learn model wrapper, availability probe. Label-free; numpy/scikit-learn imported lazily | stdlib (+ numpy, scikit-learn when present) |
| `ml_evaluation.py` | *Optional.* Trains on the synthetic training period, scores the test period against the rules, importances, sensitivity checks. Receives labels as an argument | `evaluation`, `pipeline`, `ml_triage` |
| `database.py` | All SQL (SQLite) | stdlib `sqlite3` |
| `audit.py` | SHA-256 hash chain append/verify | `database` |
| `reporting.py` | Read-side aggregates for the API | `database` |
| `service.py` | Write operations, file lock, job runner | most of the above |
| `app.py` | Flask routes, validation, errors, security headers, static files | Flask |
| `runtime_info.py` | Interpreter/kernel facts | stdlib |

The detector depends only on the Python standard library, so it is portable to any CPython 3.10+ build, including s390x. Flask (plus gunicorn in production) is the only runtime dependency of the service. The two `ml_*` modules are the only code that may use numpy and scikit-learn (imported inside functions, never at module level); the service, API and CLI call them only through `service.run_ml_evaluation`, and report "not available" with a reason when the packages cannot be imported. A test enforces that nothing else imports them and that `requirements.txt` stays free of them.

## Persistence (SQLite)

| Table | Contents |
|---|---|
| `observations` | Validated aggregate records as received (duplicates and malformed values kept so they can be reported) |
| `sites` | Location name, region, synthetic flag |
| `ingest_batches` | Each import/generation with its validation summary |
| `analysis_runs` | Run id, parameters, duration, platform, dataset fingerprint, data-quality report |
| `daily_assessments` | Current run: one row per stream-day (expected, σ, z, status, flags, context, case id) |
| `cases` | Current run: full case payload (JSON) plus indexed columns |
| `case_reviews` | Human review outcomes (status, optional role and note) — persist across re-analysis |
| `evaluation_results` | Evaluation reports |
| `audit_log` | Append-only, each row hashes the previous row's hash |
| `meta` | Data mode, seed, fingerprints, tuned thresholds and their provenance, and the optional ML triage report (`ml_triage_report`) |

WAL journal mode, `busy_timeout`, and an exclusive file lock around writes (`fcntl.flock`) keep writes safe across threads and processes. All SQL is in `database.py`; another engine (for example Db2) could be introduced by re-implementing that module. **No Db2 or other IBM service is configured in this prototype.**

## Concurrency model

- One gunicorn worker, four threads. SQLite handles concurrent readers; writers are serialised by the file lock.
- Long operations (data generation, evaluation) run in a background thread (`service.JobRunner`); the API returns `202` with a job id and the dashboard polls `/api/health`.
- Case ids are deterministic (`SITE-ABBR-YYYYMMDD` of the onset), so reviews stay attached when the analysis is re-run on the same data.

## Security

- Write endpoints require `Authorization: Bearer $SILENCE_API_TOKEN` when a token is set; otherwise they accept only loopback clients (local use or SSH tunnel) that also send an `X-Requested-With` header. That header cannot be added to a cross-site request without a CORS preflight, which blocks CSRF from other web pages open in the same browser. Behind a reverse proxy a token is mandatory, because proxied requests appear local.
- CORS is off by default; `SILENCE_CORS_ORIGINS` allow-lists exact origins (e.g. a separate frontend dev server). The frontend uses relative URLs and asset paths, so it also works behind a path prefix such as Jupyter's `/proxy/8000/`.
- Default bind address is `127.0.0.1`. Remote access is through an SSH tunnel (see the deployment guide).
- Strict Content-Security-Policy (no inline scripts; replaceable via `SILENCE_CSP` for a different frontend), `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `no-store` on API responses.
- Upload size limit (10 MB default), row limit, UTF-8 only, `.csv` only; validation before anything is stored.
- Logs contain the request line, status and size only — never headers, tokens or bodies.
- No personal data is collected; reviewer "role" replaces names; the note field warns against personal information.
