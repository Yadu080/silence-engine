"""Tamper-evident audit trail.

Every state-changing action (data generation, import, ingestion, analysis,
evaluation, case review) appends a record whose SHA-256 hash covers the
previous record's hash. ``verify`` recomputes the chain, so any edited or
deleted row is detectable. This is integrity evidence, not access control.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional

from .database import utcnow

GENESIS = "0" * 64


def _digest(prev_hash: str, ts: str, actor: str, action: str, entity_type: Optional[str],
            entity_id: Optional[str], details_json: str) -> str:
    payload = json.dumps([prev_hash, ts, actor, action, entity_type, entity_id, details_json],
                         separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append(conn: sqlite3.Connection, action: str, details: Dict[str, Any], actor: str = "system",
           entity_type: Optional[str] = None, entity_id: Optional[str] = None) -> dict:
    row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    ts = utcnow()
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)
    digest = _digest(prev, ts, actor, action, entity_type, entity_id, details_json)
    cur = conn.execute(
        "INSERT INTO audit_log (ts, actor, action, entity_type, entity_id, details_json, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (ts, actor, action, entity_type, entity_id, details_json, prev, digest))
    return {"id": cur.lastrowid, "ts": ts, "action": action, "hash": digest}


def list_entries(conn: sqlite3.Connection, limit: int = 50, offset: int = 0,
                 entity_id: Optional[str] = None) -> List[dict]:
    if entity_id:
        rows = conn.execute("SELECT * FROM audit_log WHERE entity_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                            (entity_id, limit, offset)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["details"] = json.loads(item.pop("details_json"))
        out.append(item)
    return out


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]


def verify(conn: sqlite3.Connection) -> dict:
    prev = GENESIS
    n = 0
    for r in conn.execute("SELECT * FROM audit_log ORDER BY id"):
        n += 1
        expected = _digest(prev, r["ts"], r["actor"], r["action"], r["entity_type"], r["entity_id"], r["details_json"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"valid": False, "records": n, "first_invalid_id": r["id"]}
        prev = r["hash"]
    return {"valid": True, "records": n, "first_invalid_id": None, "head_hash": prev if n else None}
