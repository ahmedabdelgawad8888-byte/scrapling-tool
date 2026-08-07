"""SQLite persistence for runs, their results, and recurring schedules.

Separate from ``ultra_scraper``'s scrape cache: that table answers "have we
fetched this URL lately", while these answer "what did the operator run, and
what came back". Keeping them in one database file means a single volume mount
is enough to persist everything.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _data_dir() -> Path:
    """Where the database lives.

    Overridable because container filesystems are ephemeral: pointing
    SCRAPLING_DATA_DIR at a mounted volume is what makes history survive a
    restart.
    """
    raw = os.getenv("SCRAPLING_DATA_DIR")
    base = Path(raw) if raw else Path(__file__).resolve().parent.parent
    base.mkdir(parents=True, exist_ok=True)
    return base


DB_PATH = _data_dir() / "scrapling_app.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    """Create the schema. Safe to call on every boot."""
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id          TEXT PRIMARY KEY,
                    kind        TEXT NOT NULL,
                    label       TEXT DEFAULT '',
                    params      TEXT NOT NULL DEFAULT '{}',
                    status      TEXT NOT NULL,
                    error       TEXT DEFAULT '',
                    total       INTEGER DEFAULT 0,
                    ok          INTEGER DEFAULT 0,
                    blocked     INTEGER DEFAULT 0,
                    errors      INTEGER DEFAULT 0,
                    started_at  REAL NOT NULL,
                    finished_at REAL,
                    schedule_id TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS run_results (
                    run_id TEXT NOT NULL,
                    idx    INTEGER NOT NULL,
                    data   TEXT NOT NULL,
                    PRIMARY KEY (run_id, idx)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_started
                    ON runs (started_at DESC);

                CREATE TABLE IF NOT EXISTS schedules (
                    id           TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    kind         TEXT NOT NULL,
                    params       TEXT NOT NULL DEFAULT '{}',
                    interval_min INTEGER NOT NULL,
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    created_at   REAL NOT NULL,
                    last_run_at  REAL,
                    next_run_at  REAL NOT NULL,
                    last_run_id  TEXT DEFAULT ''
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def create_run(kind: str, params: dict, label: str = "", schedule_id: str = "") -> str:
    run_id = uuid.uuid4().hex[:12]
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO runs (id, kind, label, params, status, started_at, schedule_id)"
                " VALUES (?, ?, ?, ?, 'running', ?, ?)",
                (run_id, kind, label, json.dumps(params, default=str), time.time(), schedule_id),
            )
            conn.commit()
        finally:
            conn.close()
    return run_id


def finish_run(run_id: str, status: str, results: list[dict], error: str = "") -> None:
    """Store the final result set and the counts the history list shows."""
    blocked = sum(1 for r in results if r.get("blocked"))
    ok = sum(
        1 for r in results
        if r.get("status") == 200 and not r.get("error") and not r.get("blocked")
    )
    errors = sum(
        1 for r in results
        if not r.get("blocked") and (r.get("error") or r.get("status") not in (200, None))
    )
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE runs SET status=?, error=?, total=?, ok=?, blocked=?, errors=?,"
                " finished_at=? WHERE id=?",
                (status, error[:500], len(results), ok, blocked, errors, time.time(), run_id),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO run_results (run_id, idx, data) VALUES (?, ?, ?)",
                [
                    (run_id, i, json.dumps(r, ensure_ascii=False, default=str))
                    for i, r in enumerate(results)
                ],
            )
            conn.commit()
        finally:
            conn.close()


def list_runs(limit: int = 60) -> list[dict]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["params"] = json.loads(item.get("params") or "{}")
        out.append(item)
    return out


def get_run(run_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            results = conn.execute(
                "SELECT data FROM run_results WHERE run_id=? ORDER BY idx", (run_id,)
            ).fetchall()
        finally:
            conn.close()
    run = dict(row)
    run["params"] = json.loads(run.get("params") or "{}")
    run["results"] = [json.loads(r["data"]) for r in results]
    return run


def delete_run(run_id: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM run_results WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
def create_schedule(name: str, kind: str, params: dict, interval_min: int) -> dict:
    sched_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO schedules (id, name, kind, params, interval_min, enabled,"
                " created_at, next_run_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    sched_id, name, kind, json.dumps(params, default=str),
                    max(1, int(interval_min)), now, now + max(1, int(interval_min)) * 60,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return get_schedule(sched_id) or {}


def list_schedules() -> list[dict]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM schedules ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["params"] = json.loads(item.get("params") or "{}")
        item["enabled"] = bool(item["enabled"])
        out.append(item)
    return out


def get_schedule(sched_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM schedules WHERE id=?", (sched_id,)).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    item = dict(row)
    item["params"] = json.loads(item.get("params") or "{}")
    item["enabled"] = bool(item["enabled"])
    return item


def set_schedule_enabled(sched_id: str, enabled: bool) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE schedules SET enabled=?, next_run_at=CASE WHEN ? THEN ? ELSE next_run_at END"
                " WHERE id=?",
                (1 if enabled else 0, 1 if enabled else 0, time.time() + 60, sched_id),
            )
            conn.commit()
        finally:
            conn.close()


def delete_schedule(sched_id: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM schedules WHERE id=?", (sched_id,))
            conn.commit()
        finally:
            conn.close()


def due_schedules(now: float | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    return [
        s for s in list_schedules()
        if s["enabled"] and float(s.get("next_run_at") or 0) <= now
    ]


def mark_schedule_ran(sched_id: str, run_id: str) -> None:
    sched = get_schedule(sched_id)
    if sched is None:
        return
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE schedules SET last_run_at=?, next_run_at=?, last_run_id=? WHERE id=?",
                (now, now + int(sched["interval_min"]) * 60, run_id, sched_id),
            )
            conn.commit()
        finally:
            conn.close()


def stats() -> dict[str, Any]:
    with _LOCK:
        conn = _connect()
        try:
            runs = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
            rows = conn.execute("SELECT COUNT(*) c FROM run_results").fetchone()["c"]
            scheds = conn.execute(
                "SELECT COUNT(*) c FROM schedules WHERE enabled=1"
            ).fetchone()["c"]
        finally:
            conn.close()
    return {"runs": runs, "results": rows, "schedules": scheds}
