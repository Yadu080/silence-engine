"""Record schema and validation for every ingestion path.

Synthetic records, CSV imports and the JSON ingestion endpoint all pass through
``validate_records`` so that the detector only ever sees normalised
``Observation`` objects.

Key rule: a missing observation is not a zero.
  * blank / "NA" / null count          -> observed_count=None, value_status="missing"
  * unparseable / negative / absurd    -> observed_count=None, value_status="malformed"
  * the literal number 0               -> observed_count=0,    value_status="ok"

``Observation`` deliberately has no field for scenario names, event ids or
ground-truth labels. Evaluation labels live in a separate file that only the
evaluation module reads.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

SITE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
SERVICE_TYPE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
MAX_COUNT = 1_000_000  # anything above this is treated as malformed
MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "-", "missing"}
TRUE_TOKENS = {"1", "true", "t", "yes", "y"}
FALSE_TOKENS = {"0", "false", "f", "no", "n"}

# Public schema (also served at /api/schema for the import UI).
SCHEMA_FIELDS: List[Dict[str, Any]] = [
    {"name": "service_date", "required": True, "type": "date (YYYY-MM-DD)",
     "description": "Calendar day the activity relates to."},
    {"name": "site_id", "required": True, "type": "string [A-Za-z0-9_.-]{1,40}",
     "description": "Aggregate service-location identifier. No personal data."},
    {"name": "service_type", "required": True, "type": "string [a-z0-9_]{1,40}",
     "description": "Service category at the location, e.g. primary_care."},
    {"name": "observed_count", "required": False, "type": "integer >= 0, or blank",
     "description": "Aggregate count for the day. Blank means NOT REPORTED (missing), which is different from 0."},
    {"name": "scheduled_open", "required": False, "type": "boolean (default true)",
     "description": "Whether the operating schedule says the service was open."},
    {"name": "feed_heartbeat_received", "required": False, "type": "boolean or blank",
     "description": "Whether the reporting system's daily heartbeat arrived."},
    {"name": "feed_last_seen", "required": False, "type": "ISO-8601 datetime",
     "description": "Last time the upstream source successfully reported."},
    {"name": "report_received_at", "required": False, "type": "ISO-8601 datetime",
     "description": "When this record arrived (used for delayed-reporting analysis)."},
    {"name": "batches_expected", "required": False, "type": "integer >= 0",
     "description": "Number of upload batches the feed should deliver for the day."},
    {"name": "batches_received", "required": False, "type": "integer >= 0",
     "description": "Number of upload batches actually delivered."},
    {"name": "weather_severity", "required": False, "type": "integer 0-3",
     "description": "Local weather severity index (0 none, 3 severe)."},
    {"name": "transport_disruption", "required": False, "type": "boolean",
     "description": "Whether a transport disruption affecting the location was reported."},
    {"name": "reported_outage", "required": False, "type": "boolean",
     "description": "Whether an operational/service outage was reported."},
    {"name": "site_name", "required": False, "type": "string <= 80",
     "description": "Human-readable location name (optional)."},
    {"name": "region", "required": False, "type": "string <= 40",
     "description": "Region used for peer comparison (optional)."},
]
SCHEMA_FIELD_NAMES = [f["name"] for f in SCHEMA_FIELDS]
REQUIRED_FIELDS = [f["name"] for f in SCHEMA_FIELDS if f["required"]]


@dataclass
class Observation:
    """A validated, aggregate service-level record. This is the model input."""

    site_id: str
    service_type: str
    service_date: date
    observed_count: Optional[int]
    value_status: str  # "ok" | "missing" | "malformed"
    raw_count_text: Optional[str] = None
    scheduled_open: bool = True
    feed_heartbeat_received: Optional[bool] = None
    feed_last_seen: Optional[datetime] = None
    report_received_at: Optional[datetime] = None
    batches_expected: Optional[int] = None
    batches_received: Optional[int] = None
    weather_severity: Optional[int] = None
    transport_disruption: Optional[bool] = None
    reported_outage: Optional[bool] = None
    source: str = "unknown"

    @property
    def stream_id(self) -> str:
        return f"{self.site_id}:{self.service_type}"

    def to_row(self) -> Dict[str, Any]:
        row = asdict(self)
        row["service_date"] = self.service_date.isoformat()
        for key in ("feed_last_seen", "report_received_at"):
            value = row[key]
            row[key] = value.isoformat() if value else None
        return row


@dataclass
class ValidationIssue:
    row: int
    field: str
    message: str
    severity: str  # "error" (row rejected) | "warning" (value nulled or flagged)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    observations: List[Observation] = field(default_factory=list)
    sites: Dict[str, Dict[str, Optional[str]]] = field(default_factory=dict)
    issues: List[ValidationIssue] = field(default_factory=list)
    rows_seen: int = 0

    @property
    def rejected(self) -> int:
        return len({i.row for i in self.issues if i.severity == "error"})

    def summary(self, max_issues: int = 50) -> Dict[str, Any]:
        return {
            "rows_seen": self.rows_seen,
            "rows_accepted": len(self.observations),
            "rows_rejected": self.rejected,
            "malformed_counts": sum(1 for o in self.observations if o.value_status == "malformed"),
            "missing_counts": sum(1 for o in self.observations if o.value_status == "missing"),
            "warnings": sum(1 for i in self.issues if i.severity == "warning"),
            "issues": [i.to_dict() for i in self.issues[:max_issues]],
            "issues_truncated": len(self.issues) > max_issues,
        }


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_bool(value: Any) -> Tuple[Optional[bool], bool]:
    """Returns (parsed, ok). Blank -> (None, True)."""
    if isinstance(value, bool):
        return value, True
    text = _text(value).lower()
    if text in MISSING_TOKENS:
        return None, True
    if text in TRUE_TOKENS:
        return True, True
    if text in FALSE_TOKENS:
        return False, True
    return None, False


def parse_int(value: Any, lo: int = 0, hi: int = MAX_COUNT) -> Tuple[Optional[int], str]:
    """Returns (parsed, status) where status is 'ok', 'missing' or 'malformed'."""
    if isinstance(value, bool):
        return None, "malformed"
    if isinstance(value, int):
        return (value, "ok") if lo <= value <= hi else (None, "malformed")
    if isinstance(value, float):
        if value != value:  # NaN
            return None, "missing"
        if value.is_integer() and lo <= value <= hi:
            return int(value), "ok"
        return None, "malformed"
    text = _text(value)
    if text.lower() in MISSING_TOKENS:
        return None, "missing"
    if re.fullmatch(r"[+]?\d+(\.0+)?", text):
        number = int(float(text))
        return (number, "ok") if lo <= number <= hi else (None, "malformed")
    return None, "malformed"


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_datetime(value: Any) -> Tuple[Optional[datetime], bool]:
    if isinstance(value, datetime):
        dt = value
    else:
        text = _text(value)
        if text.lower() in MISSING_TOKENS:
            return None, True
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None, False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc), True


def validate_records(records: Iterable[Dict[str, Any]], source: str, max_rows: int = 2_000_000) -> ValidationResult:
    """Validate raw dict records into Observations.

    Structural problems (missing site/date/service, bad identifiers) reject the
    row. Bad optional values are nulled with a warning. Bad counts are kept as
    ``malformed`` so data-quality checks can report them.
    """
    result = ValidationResult()
    for index, raw in enumerate(records, start=1):
        if index > max_rows:
            result.issues.append(ValidationIssue(index, "*", f"row limit {max_rows} exceeded; remaining rows ignored", "error"))
            break
        result.rows_seen += 1
        if not isinstance(raw, dict):
            result.issues.append(ValidationIssue(index, "*", "record is not an object", "error"))
            continue

        row_errors: List[ValidationIssue] = []
        site_id = _text(raw.get("site_id"))
        service_type = _text(raw.get("service_type")).lower()
        service_date = parse_date(raw.get("service_date"))
        if not SITE_ID_RE.fullmatch(site_id):
            row_errors.append(ValidationIssue(index, "site_id", "missing or invalid site_id", "error"))
        if not SERVICE_TYPE_RE.fullmatch(service_type):
            row_errors.append(ValidationIssue(index, "service_type", "missing or invalid service_type", "error"))
        if service_date is None:
            row_errors.append(ValidationIssue(index, "service_date", "missing or invalid date (YYYY-MM-DD)", "error"))
        if row_errors:
            result.issues.extend(row_errors)
            continue

        warnings: List[ValidationIssue] = []
        raw_count = raw.get("observed_count")
        count, status = parse_int(raw_count)
        raw_count_text = None
        if status == "malformed":
            raw_count_text = _text(raw_count)[:40]
            warnings.append(ValidationIssue(index, "observed_count", f"malformed count {raw_count_text!r} treated as missing", "warning"))

        def opt_bool(name: str, default: Optional[bool] = None) -> Optional[bool]:
            if name not in raw:
                return default
            parsed, ok = parse_bool(raw.get(name))
            if not ok:
                warnings.append(ValidationIssue(index, name, "invalid boolean ignored", "warning"))
                return default
            return default if parsed is None else parsed

        def opt_int(name: str, lo: int, hi: int) -> Optional[int]:
            if name not in raw:
                return None
            parsed, st = parse_int(raw.get(name), lo, hi)
            if st == "malformed":
                warnings.append(ValidationIssue(index, name, f"invalid value ignored (expected {lo}-{hi})", "warning"))
            return parsed

        def opt_dt(name: str) -> Optional[datetime]:
            if name not in raw:
                return None
            parsed, ok = parse_datetime(raw.get(name))
            if not ok:
                warnings.append(ValidationIssue(index, name, "invalid datetime ignored", "warning"))
            return parsed

        scheduled = opt_bool("scheduled_open", True)
        heartbeat_parsed, hb_ok = parse_bool(raw.get("feed_heartbeat_received"))
        if not hb_ok:
            warnings.append(ValidationIssue(index, "feed_heartbeat_received", "invalid boolean ignored", "warning"))
        obs = Observation(
            site_id=site_id,
            service_type=service_type,
            service_date=service_date,
            observed_count=count,
            value_status=status,
            raw_count_text=raw_count_text,
            scheduled_open=bool(scheduled) if scheduled is not None else True,
            feed_heartbeat_received=heartbeat_parsed,
            feed_last_seen=opt_dt("feed_last_seen"),
            report_received_at=opt_dt("report_received_at"),
            batches_expected=opt_int("batches_expected", 0, 10_000),
            batches_received=opt_int("batches_received", 0, 10_000),
            weather_severity=opt_int("weather_severity", 0, 3),
            transport_disruption=opt_bool("transport_disruption"),
            reported_outage=opt_bool("reported_outage"),
            source=source,
        )
        result.issues.extend(warnings)
        result.observations.append(obs)

        name = _text(raw.get("site_name"))[:80] or None
        region = _text(raw.get("region"))[:40] or None
        known = result.sites.setdefault(site_id, {"site_name": None, "region": None})
        if name and not known["site_name"]:
            known["site_name"] = name
        if region and not known["region"]:
            known["region"] = region
    return result


def parse_csv_text(text: str, source: str, max_rows: int) -> ValidationResult:
    """Validate a CSV document (header row required)."""
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [f for f in REQUIRED_FIELDS if f not in header]
    if missing:
        result = ValidationResult()
        result.issues.append(ValidationIssue(0, ",".join(missing), "CSV header is missing required columns", "error"))
        return result
    reader.fieldnames = header
    unknown = [h for h in header if h and h not in SCHEMA_FIELD_NAMES]
    result = validate_records(reader, source=source, max_rows=max_rows)
    if unknown:
        result.issues.insert(0, ValidationIssue(0, ",".join(unknown[:10]), "unknown columns ignored", "warning"))
    return result


def observations_to_csv(observations: Iterable[Observation], sites: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """Canonical CSV export (also used to fingerprint datasets)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(SCHEMA_FIELD_NAMES)
    sites = sites or {}
    for o in observations:
        meta = sites.get(o.site_id, {})
        count_text = str(o.observed_count) if o.observed_count is not None else (o.raw_count_text or "")
        writer.writerow([
            o.service_date.isoformat(), o.site_id, o.service_type, count_text,
            _b(o.scheduled_open), _b(o.feed_heartbeat_received),
            o.feed_last_seen.isoformat() if o.feed_last_seen else "",
            o.report_received_at.isoformat() if o.report_received_at else "",
            _n(o.batches_expected), _n(o.batches_received), _n(o.weather_severity),
            _b(o.transport_disruption), _b(o.reported_outage),
            meta.get("site_name") or "", meta.get("region") or "",
        ])
    return out.getvalue()


def _b(value: Optional[bool]) -> str:
    if value is None:
        return ""
    return "1" if value else "0"


def _n(value: Optional[int]) -> str:
    return "" if value is None else str(value)
