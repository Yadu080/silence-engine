"""SQLite persistence (standard-library ``sqlite3`` only).

All SQL lives here so a different database could be introduced later by
re-implementing this module. Ground-truth labels are deliberately NOT stored
in this database: the observations table contains only model inputs.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .ingest import Observation

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    site_id    TEXT PRIMARY KEY,
    site_name  TEXT,
    region     TEXT,
    synthetic  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_batches (
    batch_id      TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    mode          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    rows_accepted INTEGER NOT NULL,
    rows_rejected INTEGER NOT NULL,
    summary_json  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id                 TEXT NOT NULL,
    service_type            TEXT NOT NULL,
    service_date            TEXT NOT NULL,
    observed_count          INTEGER CHECK (observed_count IS NULL OR observed_count >= 0),
    value_status            TEXT NOT NULL CHECK (value_status IN ('ok', 'missing', 'malformed')),
    raw_count_text          TEXT,
    scheduled_open          INTEGER NOT NULL,
    feed_heartbeat_received INTEGER,
    feed_last_seen          TEXT,
    report_received_at      TEXT,
    batches_expected        INTEGER,
    batches_received        INTEGER,
    weather_severity        INTEGER,
    transport_disruption    INTEGER,
    reported_outage         INTEGER,
    source                  TEXT NOT NULL,
    ingest_batch_id         TEXT NOT NULL,
    ingested_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_stream_date ON observations (site_id, service_type, service_date);
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id              TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT NOT NULL,
    duration_ms         INTEGER NOT NULL,
    end_date            TEXT,
    params_json         TEXT NOT NULL,
    n_observations      INTEGER NOT NULL,
    n_cases             INTEGER NOT NULL,
    dataset_fingerprint TEXT,
    engine_version      TEXT NOT NULL,
    platform_json       TEXT NOT NULL,
    dq_json             TEXT NOT NULL,
    is_current          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS daily_assessments (
    run_id             TEXT NOT NULL,
    stream_id          TEXT NOT NULL,
    site_id            TEXT NOT NULL,
    service_type       TEXT NOT NULL,
    date               TEXT NOT NULL,
    observed           INTEGER,
    value_status       TEXT NOT NULL,
    status             TEXT NOT NULL,
    scheduled_open     INTEGER NOT NULL,
    schedule_inferred  INTEGER NOT NULL,
    expected           REAL,
    typical_if_open    REAL,
    sigma              REAL,
    n_history          INTEGER NOT NULL,
    z                  REAL,
    shortfall          REAL,
    shortfall_frac     REAL,
    cusum              REAL,
    trusted            INTEGER NOT NULL,
    flags              TEXT NOT NULL,
    weather            INTEGER,
    transport          INTEGER,
    outage             INTEGER,
    heartbeat          INTEGER,
    batch_completeness REAL,
    stale_days         REAL,
    case_id            TEXT,
    PRIMARY KEY (run_id, stream_id, date)
);
CREATE TABLE IF NOT EXISTS cases (
    run_id         TEXT NOT NULL,
    case_id        TEXT NOT NULL,
    stream_id      TEXT NOT NULL,
    site_id        TEXT NOT NULL,
    service_type   TEXT NOT NULL,
    category       TEXT NOT NULL,
    actionable     INTEGER NOT NULL,
    priority       INTEGER NOT NULL,
    evidence_score INTEGER NOT NULL,
    feed_status    TEXT NOT NULL,
    onset          TEXT NOT NULL,
    end_date       TEXT NOT NULL,
    alert_date     TEXT,
    ongoing        INTEGER NOT NULL,
    payload_json   TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id)
);
CREATE TABLE IF NOT EXISTS case_reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL,
    status        TEXT NOT NULL,
    note          TEXT,
    reviewer_role TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_case ON case_reviews (case_id, id);
CREATE TABLE IF NOT EXISTS evaluation_results (
    eval_id             TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    dataset_fingerprint TEXT,
    duration_ms         INTEGER NOT NULL,
    payload_json        TEXT NOT NULL,
    is_current          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    TEXT,
    details_json TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    hash         TEXT NOT NULL
);
"""

OBS_COLUMNS = (
    "site_id", "service_type", "service_date", "observed_count", "value_status", "raw_count_text",
    "scheduled_open", "feed_heartbeat_received", "feed_last_seen", "report_received_at",
    "batches_expected", "batches_received", "weather_severity", "transport_disruption",
    "reported_outage", "source", "ingest_batch_id", "ingested_at",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if get_meta(conn, "schema_version") is None:
        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_meta_json(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    raw = get_meta(conn, key)
    return json.loads(raw) if raw else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def set_meta_json(conn: sqlite3.Connection, key: str, value: Any) -> None:
    set_meta(conn, key, json.dumps(value, sort_keys=True, default=str))


def delete_meta(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))


# ----------------------------------------------------------------------------
# Sites and observations
# ----------------------------------------------------------------------------

def upsert_sites(conn: sqlite3.Connection, sites: Dict[str, Dict[str, Any]], synthetic: bool) -> None:
    now = utcnow()
    for site_id, meta in sites.items():
        conn.execute(
            """INSERT INTO sites (site_id, site_name, region, synthetic, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(site_id) DO UPDATE SET
                 site_name = COALESCE(excluded.site_name, sites.site_name),
                 region = COALESCE(excluded.region, sites.region),
                 synthetic = excluded.synthetic, updated_at = excluded.updated_at""",
            (site_id, meta.get("site_name"), meta.get("region"), int(synthetic), now))


def load_sites(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    return {r["site_id"]: {"site_name": r["site_name"], "region": r["region"], "synthetic": bool(r["synthetic"])}
            for r in conn.execute("SELECT * FROM sites ORDER BY site_id")}


def clear_data(conn: sqlite3.Connection) -> None:
    for table in ("observations", "sites", "daily_assessments", "cases", "ingest_batches"):
        conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
    conn.execute("UPDATE analysis_runs SET is_current = 0")
    conn.execute("UPDATE evaluation_results SET is_current = 0")


def insert_observations(conn: sqlite3.Connection, observations: Iterable[Observation], batch_id: str) -> int:
    now = utcnow()
    rows = []
    for o in observations:
        rows.append((
            o.site_id, o.service_type, o.service_date.isoformat(), o.observed_count, o.value_status, o.raw_count_text,
            int(o.scheduled_open), _b(o.feed_heartbeat_received),
            o.feed_last_seen.isoformat() if o.feed_last_seen else None,
            o.report_received_at.isoformat() if o.report_received_at else None,
            o.batches_expected, o.batches_received, o.weather_severity, _b(o.transport_disruption),
            _b(o.reported_outage), o.source, batch_id, now,
        ))
    placeholders = ",".join("?" for _ in OBS_COLUMNS)
    conn.executemany(f"INSERT INTO observations ({','.join(OBS_COLUMNS)}) VALUES ({placeholders})", rows)
    return len(rows)


def record_batch(conn: sqlite3.Connection, batch_id: str, source: str, mode: str, accepted: int, rejected: int,
                 summary: dict) -> None:
    conn.execute("INSERT INTO ingest_batches VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (batch_id, source, mode, utcnow(), accepted, rejected, json.dumps(summary, default=str)))


def load_observations(conn: sqlite3.Connection) -> List[Observation]:
    out: List[Observation] = []
    for r in conn.execute("SELECT * FROM observations ORDER BY site_id, service_type, service_date, id"):
        out.append(Observation(
            site_id=r["site_id"], service_type=r["service_type"], service_date=date.fromisoformat(r["service_date"]),
            observed_count=r["observed_count"], value_status=r["value_status"], raw_count_text=r["raw_count_text"],
            scheduled_open=bool(r["scheduled_open"]), feed_heartbeat_received=_ob(r["feed_heartbeat_received"]),
            feed_last_seen=_dt(r["feed_last_seen"]), report_received_at=_dt(r["report_received_at"]),
            batches_expected=r["batches_expected"], batches_received=r["batches_received"],
            weather_severity=r["weather_severity"], transport_disruption=_ob(r["transport_disruption"]),
            reported_outage=_ob(r["reported_outage"]), source=r["source"],
        ))
    return out


def observation_stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute("""SELECT COUNT(*) AS n, MIN(service_date) AS first, MAX(service_date) AS last,
                                 COUNT(DISTINCT site_id) AS sites,
                                 COUNT(DISTINCT site_id || ':' || service_type) AS streams
                          FROM observations""").fetchone()
    sources = {r["source"]: r["n"] for r in conn.execute("SELECT source, COUNT(*) AS n FROM observations GROUP BY source")}
    return {"records": row["n"], "first_date": row["first"], "last_date": row["last"], "sites": row["sites"],
            "streams": row["streams"], "by_source": sources}


def recent_batches(conn: sqlite3.Connection, limit: int = 10) -> List[dict]:
    rows = conn.execute("SELECT * FROM ingest_batches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"batch_id": r["batch_id"], "source": r["source"], "mode": r["mode"], "created_at": r["created_at"],
             "rows_accepted": r["rows_accepted"], "rows_rejected": r["rows_rejected"]} for r in rows]


# ----------------------------------------------------------------------------
# Analysis results
# ----------------------------------------------------------------------------

def save_analysis(conn: sqlite3.Connection, run: dict, day_rows: Sequence[tuple], cases: Sequence[dict]) -> None:
    conn.execute("DELETE FROM daily_assessments")
    conn.execute("DELETE FROM cases")
    conn.execute("UPDATE analysis_runs SET is_current = 0")
    conn.execute(
        """INSERT INTO analysis_runs (run_id, started_at, finished_at, duration_ms, end_date, params_json,
               n_observations, n_cases, dataset_fingerprint, engine_version, platform_json, dq_json, is_current)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (run["run_id"], run["started_at"], run["finished_at"], run["duration_ms"], run["end_date"],
         json.dumps(run["params"], sort_keys=True), run["n_observations"], len(cases), run["dataset_fingerprint"],
         run["engine_version"], json.dumps(run["platform"], sort_keys=True), json.dumps(run["dq"], default=str)))
    conn.executemany(f"INSERT INTO daily_assessments VALUES ({','.join('?' for _ in range(27))})", day_rows)
    conn.executemany(
        "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(run["run_id"], c["case_id"], c["stream_id"], c["site_id"], c["service_type"], c["category"],
          int(c["actionable"]), c["priority"], c["evidence_score"], c["feed_status"], c["onset"], c["end"],
          c["alert_date"], int(c["ongoing"]), json.dumps(c, default=str)) for c in cases])


def current_run(conn: sqlite3.Connection) -> Optional[dict]:
    r = conn.execute("SELECT * FROM analysis_runs WHERE is_current = 1 ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not r:
        return None
    return {
        "run_id": r["run_id"], "started_at": r["started_at"], "finished_at": r["finished_at"],
        "duration_ms": r["duration_ms"], "end_date": r["end_date"], "params": json.loads(r["params_json"]),
        "n_observations": r["n_observations"], "n_cases": r["n_cases"],
        "dataset_fingerprint": r["dataset_fingerprint"], "engine_version": r["engine_version"],
        "platform": json.loads(r["platform_json"]),
    }


def current_dq(conn: sqlite3.Connection) -> Optional[dict]:
    r = conn.execute("SELECT dq_json FROM analysis_runs WHERE is_current = 1 LIMIT 1").fetchone()
    return json.loads(r["dq_json"]) if r else None


def run_history(conn: sqlite3.Connection, limit: int = 10) -> List[dict]:
    rows = conn.execute("""SELECT run_id, finished_at, duration_ms, n_observations, n_cases, engine_version,
                                  platform_json FROM analysis_runs ORDER BY finished_at DESC LIMIT ?""", (limit,))
    out = []
    for r in rows:
        p = json.loads(r["platform_json"])
        out.append({"run_id": r["run_id"], "finished_at": r["finished_at"], "duration_ms": r["duration_ms"],
                    "n_observations": r["n_observations"], "n_cases": r["n_cases"],
                    "engine_version": r["engine_version"], "machine": p.get("machine"), "system": p.get("system")})
    return out


def load_day_rows(conn: sqlite3.Connection, stream_id: Optional[str] = None) -> List[sqlite3.Row]:
    if stream_id:
        return conn.execute("SELECT * FROM daily_assessments WHERE stream_id = ? ORDER BY date", (stream_id,)).fetchall()
    return conn.execute("SELECT * FROM daily_assessments ORDER BY stream_id, date").fetchall()


def load_cases(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute("SELECT payload_json FROM cases ORDER BY actionable DESC, priority DESC, onset").fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def load_case(conn: sqlite3.Connection, case_id: str) -> Optional[dict]:
    r = conn.execute("SELECT payload_json FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    return json.loads(r["payload_json"]) if r else None


# ----------------------------------------------------------------------------
# Reviews and evaluation
# ----------------------------------------------------------------------------

def add_review(conn: sqlite3.Connection, case_id: str, status: str, note: Optional[str], reviewer_role: Optional[str]) -> dict:
    created = utcnow()
    cur = conn.execute("INSERT INTO case_reviews (case_id, status, note, reviewer_role, created_at) VALUES (?, ?, ?, ?, ?)",
                       (case_id, status, note, reviewer_role, created))
    return {"id": cur.lastrowid, "case_id": case_id, "status": status, "note": note, "reviewer_role": reviewer_role,
            "created_at": created}


def reviews_for(conn: sqlite3.Connection, case_id: str) -> List[dict]:
    rows = conn.execute("SELECT * FROM case_reviews WHERE case_id = ? ORDER BY id DESC", (case_id,)).fetchall()
    return [dict(r) for r in rows]


def latest_review_status(conn: sqlite3.Connection) -> Dict[str, dict]:
    rows = conn.execute("""SELECT r.* FROM case_reviews r
                           JOIN (SELECT case_id, MAX(id) AS mid FROM case_reviews GROUP BY case_id) m
                             ON r.id = m.mid""").fetchall()
    return {r["case_id"]: dict(r) for r in rows}


def save_evaluation(conn: sqlite3.Connection, eval_id: str, report: dict) -> None:
    conn.execute("UPDATE evaluation_results SET is_current = 0")
    conn.execute("INSERT INTO evaluation_results VALUES (?, ?, ?, ?, ?, 1)",
                 (eval_id, report.get("generated_at") or utcnow(), report.get("dataset_fingerprint"),
                  int(1000 * report.get("duration_seconds", 0)), json.dumps(report, default=str)))


def current_evaluation(conn: sqlite3.Connection) -> Optional[dict]:
    r = conn.execute("SELECT eval_id, payload_json FROM evaluation_results WHERE is_current = 1 LIMIT 1").fetchone()
    if not r:
        return None
    payload = json.loads(r["payload_json"])
    payload["eval_id"] = r["eval_id"]
    return payload


def db_status(conn: sqlite3.Connection, path: Path) -> dict:
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    tables = {}
    for t in ("observations", "sites", "daily_assessments", "cases", "case_reviews", "evaluation_results",
              "analysis_runs", "audit_log", "ingest_batches"):
        tables[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
    size = path.stat().st_size if path.exists() else 0
    wal = Path(str(path) + "-wal")
    return {"engine": "SQLite", "sqlite_version": sqlite3.sqlite_version, "path": str(path),
            "size_bytes": size + (wal.stat().st_size if wal.exists() else 0), "integrity": integrity,
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "schema_version": get_meta(conn, "schema_version"), "row_counts": tables}


def _b(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(bool(value))


def _ob(value: Any) -> Optional[bool]:
    return None if value is None else bool(value)


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None
