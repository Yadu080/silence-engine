from datetime import date

import pytest

from silence_engine import ingest, stats


def test_median_mad_quantile():
    assert stats.median([3, 1, 2]) == 2
    assert stats.median([4, 1, 2, 3]) == 2.5
    assert stats.mad([1, 2, 3, 4, 100]) == 1
    assert stats.quantile([0, 10], 0.5) == 5
    with pytest.raises(ValueError):
        stats.median([])


def test_wilson_interval_contains_estimate_and_handles_zero_n():
    lo, hi = stats.wilson_interval(8, 10)
    assert lo < 0.8 < hi
    assert stats.wilson_interval(0, 0) == (None, None)


def test_flatline_run_length_adapts_to_volume():
    # Repeats are common at low volume, so a longer run is needed before flagging.
    assert stats.flatline_run_length(5) > stats.flatline_run_length(200)
    assert 3 <= stats.flatline_run_length(200) <= 10


def test_safe_div():
    assert stats.safe_div(1, 0) is None
    assert stats.safe_div(1, 4) == 0.25


def test_missing_is_not_zero():
    res = ingest.validate_records([
        {"service_date": "2026-01-05", "site_id": "A", "service_type": "clinic", "observed_count": ""},
        {"service_date": "2026-01-06", "site_id": "A", "service_type": "clinic", "observed_count": "0"},
        {"service_date": "2026-01-07", "site_id": "A", "service_type": "clinic", "observed_count": "NA"},
    ], source="t")
    blank, zero, na = res.observations
    assert blank.observed_count is None and blank.value_status == "missing"
    assert zero.observed_count == 0 and zero.value_status == "ok"
    assert na.observed_count is None and na.value_status == "missing"


@pytest.mark.parametrize("raw", ["-3", "12O", "#REF!", "4.5", "999999999"])
def test_malformed_counts_are_kept_as_malformed(raw):
    res = ingest.validate_records([{"service_date": "2026-01-05", "site_id": "A", "service_type": "clinic",
                                    "observed_count": raw}], source="t")
    o = res.observations[0]
    assert o.observed_count is None and o.value_status == "malformed" and o.raw_count_text == raw
    assert any(i.field == "observed_count" for i in res.issues)


def test_structural_errors_reject_rows():
    res = ingest.validate_records([
        {"service_date": "2026-13-01", "site_id": "A", "service_type": "clinic"},
        {"service_date": "2026-01-01", "site_id": "bad id!", "service_type": "clinic"},
        {"service_date": "2026-01-01", "site_id": "A", "service_type": "Bad Type"},
        "not a dict",
    ], source="t")
    assert res.observations == []
    assert res.rejected == 4


def test_csv_requires_header_columns():
    res = ingest.parse_csv_text("site_id,observed_count\nA,3\n", source="t", max_rows=100)
    assert not res.observations
    assert res.issues[0].row == 0 and res.issues[0].severity == "error"


def test_csv_round_trip_and_booleans():
    text = ("service_date,site_id,service_type,observed_count,scheduled_open,feed_heartbeat_received,extra\n"
            "2026-01-05,A,clinic,12,yes,false,x\n2026-01-06,A,clinic,,0,\n")
    res = ingest.parse_csv_text(text, source="t", max_rows=100)
    a, b = res.observations
    assert a.scheduled_open is True and a.feed_heartbeat_received is False and a.observed_count == 12
    assert b.scheduled_open is False and b.observed_count is None
    assert any("unknown columns" in i.message for i in res.issues)
    exported = ingest.observations_to_csv(res.observations)
    again = ingest.parse_csv_text(exported, source="t", max_rows=100).observations
    assert [(o.service_date, o.observed_count) for o in again] == [(date(2026, 1, 5), 12), (date(2026, 1, 6), None)]


def test_row_limit_enforced():
    rows = [{"service_date": "2026-01-05", "site_id": "A", "service_type": "c", "observed_count": 1}] * 5
    res = ingest.validate_records(rows, source="t", max_rows=3)
    assert len(res.observations) == 3
    assert any("row limit" in i.message for i in res.issues)


def test_relative_config_paths_resolve_against_project_root():
    from silence_engine.config import PROJECT_ROOT, load_settings

    s = load_settings({"SILENCE_DATA_DIR": "./data", "SILENCE_API_TOKEN": "  "})
    assert s.data_dir == (PROJECT_ROOT / "data").resolve()
    assert s.db_path.parent == s.data_dir
    assert s.api_token is None  # blank token means "not configured"
    assert "api_token" not in s.describe()
