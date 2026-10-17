import io
import platform

import pytest

from silence_engine import audit, database, service
from silence_engine.app import create_app

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
REMOTE = {"REMOTE_ADDR": "203.0.113.9"}
XRW = {"X-Requested-With": "SilenceEngine"}  # anti-CSRF header the dashboard sends on writes


def test_health_and_system_info_report_actual_runtime(client):
    h = client.get("/api/health").get_json()
    assert h["status"] == "ok" and h["data_loaded"] and h["analysis_ready"] and h["synthetic_active"]
    info = client.get("/api/system-info").get_json()
    assert info["runtime"]["machine"] == platform.machine()
    assert info["runtime"]["python_version"] == platform.python_version()
    assert info["runtime"]["is_s390x"] == (platform.machine() == "s390x")
    assert info["database"]["integrity"] == "ok"
    assert info["audit"]["valid"] is True
    assert "api_token" not in str(info["config"]).replace("api_token_configured", "")


def test_summary_counts_match_case_queue(client):
    s = client.get("/api/summary?window=28").get_json()
    lo = s["window_start"]
    cases = client.get(f"/api/cases?since={lo}&limit=500").get_json()["items"]
    assert s["potential_access_gaps"] == sum(1 for c in cases if c["category"] == "POTENTIAL_ACCESS_GAP")
    assert s["reporting_problems"] == sum(1 for c in cases if c["category"] == "REPORTING_PROBLEM")
    assert s["recent_actionable_cases"] == len(cases)
    assert s["sites_monitored"] == 9
    ledger = s["ledger"]
    assert ledger["unaccounted_in_cases"] == sum(ledger["by_category"][k] for k in
                                                 ("POTENTIAL_ACCESS_GAP", "REPORTING_PROBLEM", "UNCERTAIN"))


def test_case_list_filters_and_validation(client):
    all_cases = client.get("/api/cases?limit=500").get_json()
    assert all(c["category"] != "EXPECTED_VARIATION" for c in all_cases["items"])
    gaps = client.get("/api/cases?category=POTENTIAL_ACCESS_GAP").get_json()["items"]
    assert gaps and all(c["category"] == "POTENTIAL_ACCESS_GAP" for c in gaps)
    pr = [c["priority"] for c in all_cases["items"]]
    assert pr == sorted(pr, reverse=True)
    for bad in ("category=NOPE", "limit=0", "limit=x", "feed_status=bad", "sort=evil", "since=2026-99-01"):
        r = client.get(f"/api/cases?{bad}")
        assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_parameter"


def test_case_detail_has_evidence_and_404(client):
    first = client.get("/api/cases?limit=1").get_json()["items"][0]
    d = client.get(f"/api/cases/{first['case_id']}").get_json()
    c = d["case"]
    for key in ("evidence", "hypotheses", "limitations", "verification_steps", "supporting", "conflicting"):
        assert key in c
    assert d["timeline"] and any(t["case_id"] == c["case_id"] for t in d["timeline"])
    assert client.get("/api/cases/NOPE-X-20990101").status_code == 404
    assert client.get("/api/cases/bad%20id!").status_code == 400


def test_timeseries_and_silence_map_agree(client):
    agg = client.get("/api/timeseries?stream_id=ALL").get_json()
    assert agg["aggregate"] and agg["days"]
    smap = client.get("/api/silence-map").get_json()
    row = smap["streams"][0]
    ts = client.get(f"/api/timeseries?stream_id={row['stream_id']}").get_json()["days"]
    codes = smap["status_codes"]
    by_date = {d: cell for d, cell in zip(smap["dates"], row["cells"]) if cell}
    for day in ts:
        assert by_date[day["date"]][0] == codes[day["status"]]
    assert client.get("/api/timeseries?stream_id=../../etc").status_code == 400
    assert client.get("/api/timeseries?stream_id=ZZZ:none").status_code == 404


def test_data_quality_is_separate_from_service_alerts(client):
    dq = client.get("/api/data-quality").get_json()
    assert dq["streams"] and dq["issues"]
    types = {i["type"] for i in dq["issues"]}
    assert {"missing_record", "duplicate", "malformed"} <= types
    assert dq["feed_matrix"]["dates"]


def test_evaluation_available_and_synthetic(client):
    ev = client.get("/api/evaluation").get_json()
    assert ev["available"] and ev["report"]["synthetic"] is True
    assert ev["report"]["test"]["engine"]["detection"]["alertable_events"] > 0


def test_review_creates_audit_entry_and_chain_stays_valid(client, initialised_settings):
    case_id = client.get("/api/cases?limit=1").get_json()["items"][0]["case_id"]
    r = client.post(f"/api/cases/{case_id}/review", json={"status": "under_review", "note": "calling site",
                                                          "reviewer_role": "regional analyst"},
                    environ_base=LOCAL, headers=XRW)
    assert r.status_code == 201
    d = client.get(f"/api/cases/{case_id}").get_json()
    assert d["reviews"][0]["status"] == "under_review"
    assert any(e["action"] == "case_reviewed" for e in d["audit"])
    assert client.get("/api/audit/verify").get_json()["valid"] is True
    bad = client.post(f"/api/cases/{case_id}/review", json={"status": "guilty"}, environ_base=LOCAL, headers=XRW)
    assert bad.status_code == 400
    long_note = client.post(f"/api/cases/{case_id}/review", json={"status": "dismissed", "note": "x" * 1001},
                            environ_base=LOCAL, headers=XRW)
    assert long_note.status_code == 400


def test_writes_refused_from_remote_without_token(client):
    case_id = client.get("/api/cases?limit=1").get_json()["items"][0]["case_id"]
    r = client.post(f"/api/cases/{case_id}/review", json={"status": "dismissed"}, environ_base=REMOTE)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "forbidden"
    assert client.post("/api/generate-demo-data", json={}, environ_base=REMOTE).status_code == 403


def test_token_required_when_configured(initialised_settings):
    from dataclasses import replace
    settings = replace(initialised_settings, api_token="s3cret-token-for-tests")
    app = create_app(settings, start_background=False)
    c = app.test_client()
    case_id = c.get("/api/cases?limit=1").get_json()["items"][0]["case_id"]
    assert c.post(f"/api/cases/{case_id}/review", json={"status": "dismissed"}, environ_base=LOCAL, headers=XRW).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert c.post(f"/api/cases/{case_id}/review", json={"status": "dismissed"}, headers=wrong).status_code == 401
    ok = {"Authorization": "Bearer s3cret-token-for-tests"}
    assert c.post(f"/api/cases/{case_id}/review", json={"status": "dismissed"}, headers=ok,
                  environ_base=REMOTE).status_code == 201


def test_invalid_json_and_unknown_endpoint(client):
    case_id = client.get("/api/cases?limit=1").get_json()["items"][0]["case_id"]
    r = client.post(f"/api/cases/{case_id}/review", data="{not json", content_type="application/json",
                    environ_base=LOCAL, headers=XRW)
    assert r.status_code == 400
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/api/does-not-exist").get_json()["error"]["code"] == "not_found"


def test_generate_demo_validates_seed(client):
    r = client.post("/api/generate-demo-data", json={"seed": "abc"}, environ_base=LOCAL, headers=XRW)
    assert r.status_code == 400


def test_csv_import_pathway(make_settings):
    settings = make_settings()
    service.generate_demo(settings, seed=3, actor="test")
    app = create_app(settings, start_background=False)
    c = app.test_client()
    csv_text = ("service_date,site_id,service_type,observed_count,scheduled_open,feed_heartbeat_received,site_name,region\n"
                "2026-10-01,NEW1,food_distribution,40,1,1,New Pantry,East\n"
                "2026-10-02,NEW1,food_distribution,,1,0,New Pantry,East\n"
                "2026-10-03,NEW1,food_distribution,-5,1,1,New Pantry,East\n"
                "bad-date,NEW1,food_distribution,4,1,1,New Pantry,East\n")
    r = c.post("/api/import-csv", data={"file": (io.BytesIO(csv_text.encode()), "extra.csv"), "mode": "append"},
               content_type="multipart/form-data", environ_base=LOCAL, headers=XRW)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    v = body["validation"]
    assert v["rows_accepted"] == 3 and v["rows_rejected"] == 1 and v["malformed_counts"] == 1 and v["missing_counts"] == 1
    assert body["data_mode"] == "mixed"
    streams = [s["stream_id"] for s in c.get("/api/sites").get_json()["streams"]]
    assert "NEW1:food_distribution" in streams
    bad_header = c.post("/api/import-csv", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")},
                        content_type="multipart/form-data", environ_base=LOCAL, headers=XRW)
    assert bad_header.status_code == 422
    not_csv = c.post("/api/import-csv", data={"file": (io.BytesIO(b"x"), "x.txt")},
                     content_type="multipart/form-data", environ_base=LOCAL, headers=XRW)
    assert not_csv.status_code == 400
    with service.connection(settings) as conn:
        assert any(e["action"] == "records_imported" for e in audit.list_entries(conn))


def test_json_ingest_endpoint(make_settings):
    settings = make_settings()
    service.generate_demo(settings, seed=5, actor="test")
    c = create_app(settings, start_background=False).test_client()
    r = c.post("/api/ingest", json={"records": [{"service_date": "2026-10-04", "site_id": "API1",
                                                 "service_type": "clinic", "observed_count": 12}]},
               environ_base=LOCAL, headers=XRW)
    assert r.status_code == 201
    assert c.post("/api/ingest", json={"records": []}, environ_base=LOCAL, headers=XRW).status_code == 400


def test_not_ready_state_before_any_data(make_settings):
    settings = make_settings()
    c = create_app(settings, start_background=False).test_client()
    assert c.get("/api/health").get_json()["data_loaded"] is False
    r = c.get("/api/summary")
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "not_ready"
    assert c.get("/api/evaluation").get_json()["available"] is False
    # The SPA fallback page explains how to build the frontend.
    assert b"API is running" in c.get("/").data


def test_writes_require_anti_csrf_header(client):
    case_id = client.get("/api/cases?limit=1").get_json()["items"][0]["case_id"]
    r = client.post(f"/api/cases/{case_id}/review", json={"status": "under_review"}, environ_base=LOCAL)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "csrf_header_missing"
    # A cross-site "simple" request (no custom header) cannot regenerate data either.
    assert client.post("/api/generate-demo-data", environ_base=LOCAL).status_code == 403
    assert client.post("/api/analysis/run", environ_base=LOCAL).status_code == 403


def test_cors_is_off_by_default_and_allowlisted_when_configured(initialised_settings):
    from dataclasses import replace
    plain = create_app(initialised_settings, start_background=False).test_client()
    r = plain.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert "Access-Control-Allow-Origin" not in r.headers
    assert plain.options("/api/health", headers={"Origin": "http://localhost:3000"}).status_code == 403

    settings = replace(initialised_settings, cors_origins=("http://localhost:3000",))
    c = create_app(settings, start_background=False).test_client()
    pre = c.options("/api/cases", headers={"Origin": "http://localhost:3000",
                                            "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "content-type,x-requested-with"})
    assert pre.status_code == 204
    assert pre.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "X-Requested-With" in pre.headers["Access-Control-Allow-Headers"]
    evil = c.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in evil.headers


def test_csp_can_be_overridden_for_other_frontends(initialised_settings):
    from dataclasses import replace
    c = create_app(replace(initialised_settings, csp="default-src 'self'"), start_background=False).test_client()
    assert c.get("/api/health").headers["Content-Security-Policy"] == "default-src 'self'"
