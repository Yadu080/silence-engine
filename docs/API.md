# API reference

Base path `/api`. JSON in and out (except CSV upload/export). Errors always look like:

```json
{"error": {"code": "invalid_parameter", "message": "'limit' must be between 1 and 500", "details": null}}
```

Common codes: `invalid_parameter` (400), `invalid_json` (400), `unauthorized` (401), `forbidden` (403), `csrf_header_missing` (403), `not_found` (404), `not_ready` (409, no analysis yet), `job_running` (409), `ml_unavailable` (409), `ml_insufficient_data` (409), `payload_too_large` (413), `validation_failed` (422), `internal_error` (500).

**Write access**: endpoints marked ✎ need `Authorization: Bearer <SILENCE_API_TOKEN>` when a token is configured. Without a token they accept only requests that come from the loopback interface **and** carry an `X-Requested-With` header (any value). A malicious web page cannot add that header to a cross-site request without a CORS preflight, which the server refuses, so it cannot drive the local API (CSRF).

**Paths and origins**: clients should use relative URLs (`api/...`) so the same frontend works behind a path prefix such as Jupyter's `/proxy/8000/`. CORS is off by default; `SILENCE_CORS_ORIGINS` lists exact origins (for example a separate frontend dev server) that may call the API.

## Status

| Method & path | Description |
|---|---|
| `GET /api/health` | Database status, whether data/analysis/evaluation are ready, data mode, current background job, brief runtime (machine, OS, Python) |
| `GET /api/system-info` | Verified runtime facts (architecture, `/proc/sysinfo` on IBM Z, versions), database status and row counts, data fingerprint and manifest, analysis run history, active thresholds and their provenance, audit-chain verification, safe config summary |
| `GET /api/schema` | CSV/JSON record schema |
| `GET /api/jobs/<job_id>` | Background job status (`running` / `succeeded` / `failed`, message) |

## Analysis results

| Method & path | Parameters | Description |
|---|---|---|
| `GET /api/summary` | `window` 7–120 (default 28) | Monitored counts, investigations needing attention, category counts, feeds not healthy, recent activity change, silence ledger (unaccounted expected activity by category), top cases |
| `GET /api/sites` | — | Monitored streams with site name, region, period and typical daily count |
| `GET /api/cases` | `category` (comma list of `POTENTIAL_ACCESS_GAP`, `REPORTING_PROBLEM`, `UNCERTAIN`, `EXPECTED_VARIATION`), `include_expected` bool, `site_id`, `feed_status` (`HEALTHY`/`DEGRADED`/`FAILED`), `ongoing` bool, `since` date, `review` (`open`/`reviewed`), `sort` (`priority`, `onset`, `shortfall`, `evidence`, `duration`), `order` (`asc`/`desc`), `limit` 1–500, `offset` | Investigation queue with totals per category |
| `GET /api/cases/<case_id>` | — | Full case: evidence, hypotheses, limitations, verification steps, scores with components, day-by-day timeline (35 days before to 14 after), reviews, audit entries, related cases |
| `GET /api/timeseries` | `stream_id` (`ALL` or `SITE:service_type`), `start`, `end` | Per-day observed, expected, ±2σ range, status, flags, context, case id — or system-wide aggregates for `ALL` |
| `GET /api/silence-map` | `start`, `end` | Compact stream × day matrix: `[status_code, gap_fraction, case_index]` per cell |
| `GET /api/data-quality` | `days` 14–400 (default 90) | Feed health per stream, issue runs, issue-day totals, feed-status matrix |
| `GET /api/evaluation` | — | Latest evaluation report (synthetic data only) or `available: false` with a reason |
| `GET /api/ml-triage` | — | Optional ML triage benchmark. `{"available": false, "reason": "..."}` when numpy/scikit-learn cannot be imported by the backend's Python (or `SILENCE_ML_TRIAGE=0`); `{"available": true, "report": null}` when not run yet; otherwise `{"available": true, "report": {...}, "stale": bool}` with the side-by-side comparison against the rule-based triage. Never changes cases or scores |
| `GET /api/audit` | `limit`, `offset` | Audit entries, newest first |
| `GET /api/audit/verify` | — | Recomputes the hash chain: `{valid, records, first_invalid_id, head_hash}` |
| `GET /api/export/observations.csv` | — | The loaded observations in the import schema |

## Actions ✎

| Method & path | Body | Result |
|---|---|---|
| `POST /api/generate-demo-data` | `{"seed": 42}` | `202` + job: regenerate synthetic data, evaluate, analyse |
| `POST /api/evaluation/run` | — | `202` + job: re-run evaluation (re-tunes thresholds), then analysis, then the ML benchmark when the ML stack is available |
| `POST /api/ml-triage/run` | — | `202` + job: train the optional ML triage model on the synthetic training period and compare it with the rules (about 5 s); `409 ml_unavailable` when the ML stack is missing |
| `POST /api/analysis/run` | — | Re-run detection and triage synchronously (< 1 s for the demo) |
| `POST /api/import-csv` | multipart: `file` (.csv, UTF-8), `mode` = `append`\|`replace` | `201` with validation summary and new analysis; `422` if the header is invalid or no row is valid |
| `POST /api/ingest` | `{"records": [ {...}, ... ], "reanalyze": true}` (≤ 10,000) | `201`, same validation as CSV |
| `POST /api/cases/<case_id>/review` | `{"status": "under_review" \| "verified_access_gap" \| "verified_reporting_issue" \| "verified_expected" \| "dismissed", "note": "≤1000 chars", "reviewer_role": "≤60 chars"}` | `201` with the stored review; appended to the audit chain |

## Examples

```bash
curl -s "http://127.0.0.1:8000/api/cases?category=POTENTIAL_ACCESS_GAP&limit=5"
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/ingest -H 'X-Requested-With: curl' -H 'Content-Type: application/json' -d '{"records":[{"service_date":"2026-10-05","site_id":"NGT","service_type":"primary_care","observed_count":"","feed_heartbeat_received":false}]}'
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/import-csv -H 'X-Requested-With: curl' -F file=@my_counts.csv -F mode=append
```
