"""Persistence for runs, their results, and recurring schedules.

Separate from ``ultra_scraper``'s scrape cache: that table answers "have we
fetched this URL lately", while these answer "what did the operator run, and
what came back".

Two backends sit behind one API:

* **SQLite** (default) — a file next to the app. Zero setup, but container
  filesystems are ephemeral, so history dies with the instance unless the
  directory is a mounted volume.
* **Postgres** — used when ``SUPABASE_DB_URL`` (or ``DATABASE_URL``) is set.
  History and schedules then outlive the container entirely, which is what
  makes run history survive a redeploy or an idle spin-down on a host with no
  persistent disk.

The backend is chosen once at import. Callers never know which one they got:
every function below returns the same shapes either way, including epoch-float
timestamps, so the dashboard's JSON contract is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import suppress as _suppress
from pathlib import Path
from typing import Any, cast

_log = logging.getLogger(__name__)

_LOCK = threading.Lock()

# Postgres object names are qualified so the app's tables cannot collide with
# anything else in the database, and so `public` stays empty — PostgREST exposes
# `public` to the anon key, and scrape history has no business being reachable
# that way.
_PG_SCHEMA = "scrapling"


def _data_dir() -> Path:
    """Where the SQLite database lives.

    Overridable because container filesystems are ephemeral: pointing
    SCRAPLING_DATA_DIR at a mounted volume is what makes history survive a
    restart. Irrelevant on the Postgres backend.
    """
    raw = os.getenv("SCRAPLING_DATA_DIR")
    base = Path(raw) if raw else Path(__file__).resolve().parent.parent
    base.mkdir(parents=True, exist_ok=True)
    return base


DB_PATH = _data_dir() / "scrapling_app.db"


def _dsn() -> str:
    """The Postgres DSN, or "" to stay on SQLite.

    SUPABASE_DB_URL is checked first so that a project already using
    DATABASE_URL for something else can opt in without a collision.
    """
    return (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _counts(results: list[dict]) -> tuple[int, int, int]:
    """Split a result set into (ok, blocked, errors).

    A soft-blocked row answers HTTP 200 with a captcha or login wall, so
    `blocked` has to be taken out before `ok` is counted or a fully blocked run
    reports as a clean success.
    """
    blocked = sum(1 for r in results if r.get("blocked"))
    ok = sum(
        1 for r in results
        if r.get("status") == 200 and not r.get("error") and not r.get("blocked")
    )
    errors = sum(
        1 for r in results
        if not r.get("blocked") and (r.get("error") or r.get("status") not in (200, None))
    )
    return ok, blocked, errors


def _as_dict(value: Any) -> dict:
    """Normalise a params/data column.

    SQLite stores JSON as text; Postgres hands back an already-decoded dict from
    jsonb. Both arrive here so callers see a dict regardless of backend.
    """
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _encode(obj: Any) -> str:
    """JSON for storage. `default=str` keeps a stray datetime from killing a run."""
    return json.dumps(obj, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------
class _SqliteBackend:
    """The original file-backed store. Unchanged behaviour."""

    kind = "sqlite"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def describe(self) -> str:
        return f"sqlite:{DB_PATH}"

    def init(self) -> None:
        with _LOCK:
            conn = self._connect()
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

                    CREATE INDEX IF NOT EXISTS idx_schedules_due
                        ON schedules (next_run_at) WHERE enabled = 1;

                    CREATE TABLE IF NOT EXISTS jobs (
                        id              TEXT PRIMARY KEY,
                        name            TEXT NOT NULL,
                        description     TEXT NOT NULL DEFAULT '',
                        kind            TEXT NOT NULL,
                        params          TEXT NOT NULL DEFAULT '{}',
                        options         TEXT NOT NULL DEFAULT '{}',
                        tags            TEXT NOT NULL DEFAULT '[]',
                        pinned          INTEGER NOT NULL DEFAULT 0,
                        archived        INTEGER NOT NULL DEFAULT 0,
                        run_count       INTEGER NOT NULL DEFAULT 0,
                        last_run_id     TEXT NOT NULL DEFAULT '',
                        last_run_at     REAL,
                        last_run_status TEXT NOT NULL DEFAULT '',
                        created_at      REAL NOT NULL,
                        updated_at      REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_jobs_updated
                        ON jobs (updated_at DESC);

                    CREATE TABLE IF NOT EXISTS audit_log (
                        id          TEXT PRIMARY KEY,
                        actor       TEXT NOT NULL DEFAULT '',
                        action      TEXT NOT NULL,
                        object_kind TEXT NOT NULL DEFAULT '',
                        object_id   TEXT NOT NULL DEFAULT '',
                        detail      TEXT NOT NULL DEFAULT '',
                        created_at  REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_audit_created
                        ON audit_log (created_at DESC);

                    CREATE TABLE IF NOT EXISTS notifications (
                        id         TEXT PRIMARY KEY,
                        level      TEXT NOT NULL,
                        kind       TEXT NOT NULL,
                        message    TEXT NOT NULL,
                        read       INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_notif_created
                        ON notifications (created_at DESC);

                    CREATE TABLE IF NOT EXISTS settings (
                        key        TEXT PRIMARY KEY,
                        value      TEXT NOT NULL DEFAULT 'null',
                        updated_at REAL NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # -- runs --------------------------------------------------------------
    def create_run(self, run_id, kind, params, label, schedule_id, now) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO runs (id, kind, label, params, status, started_at, schedule_id)"
                    " VALUES (?, ?, ?, ?, 'running', ?, ?)",
                    (run_id, kind, label, _encode(params), now, schedule_id),
                )
                conn.commit()
            finally:
                conn.close()

    def finish_run(self, run_id, status, results, error, counts, now) -> None:
        ok, blocked, errors = counts
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE runs SET status=?, error=?, total=?, ok=?, blocked=?, errors=?,"
                    " finished_at=? WHERE id=?",
                    (status, error[:500], len(results), ok, blocked, errors, now, run_id),
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO run_results (run_id, idx, data) VALUES (?, ?, ?)",
                    [(run_id, i, _encode(r)) for i, r in enumerate(results)],
                )
                conn.commit()
            finally:
                conn.close()

    def list_runs(self, limit) -> list[dict]:
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
                ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    def get_run(self, run_id) -> tuple[dict, list] | None:
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    return None
                results = conn.execute(
                    "SELECT data FROM run_results WHERE run_id=? ORDER BY idx", (run_id,)
                ).fetchall()
            finally:
                conn.close()
        return dict(row), [r["data"] for r in results]

    def delete_run(self, run_id) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM run_results WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
                conn.commit()
            finally:
                conn.close()

    # -- schedules ---------------------------------------------------------
    def create_schedule(self, sched_id, name, kind, params, interval_min, now) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO schedules (id, name, kind, params, interval_min, enabled,"
                    " created_at, next_run_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (sched_id, name, kind, _encode(params), interval_min,
                     now, now + interval_min * 60),
                )
                conn.commit()
            finally:
                conn.close()

    def list_schedules(self) -> list[dict]:
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM schedules ORDER BY created_at DESC"
                ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    def get_schedule(self, sched_id) -> dict | None:
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM schedules WHERE id=?", (sched_id,)
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row is not None else None

    def set_schedule_enabled(self, sched_id, enabled, next_run_at) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                if enabled:
                    conn.execute(
                        "UPDATE schedules SET enabled=1, next_run_at=? WHERE id=?",
                        (next_run_at, sched_id),
                    )
                else:
                    conn.execute(
                        "UPDATE schedules SET enabled=0 WHERE id=?", (sched_id,)
                    )
                conn.commit()
            finally:
                conn.close()

    def delete_schedule(self, sched_id) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM schedules WHERE id=?", (sched_id,))
                conn.commit()
            finally:
                conn.close()

    def mark_schedule_ran(self, sched_id, run_id, now, next_run_at) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE schedules SET last_run_at=?, next_run_at=?, last_run_id=?"
                    " WHERE id=?",
                    (now, next_run_at, run_id, sched_id),
                )
                conn.commit()
            finally:
                conn.close()

    def stats(self) -> dict[str, Any]:
        with _LOCK:
            conn = self._connect()
            try:
                runs = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
                rows = conn.execute("SELECT COUNT(*) c FROM run_results").fetchone()["c"]
                scheds = conn.execute(
                    "SELECT COUNT(*) c FROM schedules WHERE enabled=1"
                ).fetchone()["c"]
                saved = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
                unread = conn.execute(
                    "SELECT COUNT(*) c FROM notifications WHERE read=0"
                ).fetchone()["c"]
            finally:
                conn.close()
        return {
            "runs": runs, "results": rows, "schedules": scheds,
            "saved_jobs": saved, "unread": unread,
        }

    # -- settings -----------------------------------------------------------
    def set_setting(self, key, value, now):
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
                    " ON CONFLICT (key) DO UPDATE SET value=excluded.value,"
                    " updated_at=excluded.updated_at",
                    (key, _encode(value), now),
                )
                conn.commit()
            finally:
                conn.close()

    def get_setting(self, key):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key=?", (key,)
                ).fetchone()
            finally:
                conn.close()
        return _as_dict(row["value"]) if row is not None else None

    # -- saved jobs ---------------------------------------------------------
    def list_jobs(self, include_archived) -> list[dict]:
        with _LOCK:
            conn = self._connect()
            try:
                if include_archived:
                    rows = conn.execute(
                        "SELECT * FROM jobs ORDER BY pinned DESC, updated_at DESC"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM jobs WHERE archived=0"
                        " ORDER BY pinned DESC, updated_at DESC"
                    ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    def get_job(self, job_id) -> dict | None:
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            finally:
                conn.close()
        return dict(row) if row is not None else None

    def create_job(self, job_id, name, description, kind, params, options, tags,
                   pinned, actor, now) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO jobs (id, name, description, kind, params, options,"
                    " tags, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, name, description, kind, _encode(params), _encode(options),
                     _encode(tags), int(pinned), now, now),
                )
                conn.commit()
            finally:
                conn.close()

    def update_job(self, job_id, fields, now) -> None:
        """`fields` maps column name -> value. JSON columns arrive as dicts."""
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    f"UPDATE jobs SET {cols}, updated_at=? WHERE id=?",
                    (*fields.values(), now, job_id),
                )
                conn.commit()
            finally:
                conn.close()

    def touch_job(self, job_id, run_id, status, now) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE jobs SET run_count=run_count+1, last_run_id=?,"
                    " last_run_at=?, last_run_status=?, updated_at=? WHERE id=?",
                    (run_id, now, status, now, job_id),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_job(self, job_id) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                conn.commit()
            finally:
                conn.close()

    # -- audit ------------------------------------------------------------
    def log_audit(self, audit_id, actor, action, object_kind, object_id, detail, now) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO audit_log (id, actor, action, object_kind, object_id,"
                    " detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (audit_id, actor, action, object_kind, object_id, detail[:2000], now),
                )
                conn.commit()
            finally:
                conn.close()

    def list_audit(self, limit) -> list[dict]:
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    # -- notifications ------------------------------------------------------
    def add_notification(self, notif_id, level, kind, message, now) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO notifications (id, level, kind, message, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (notif_id, level, kind, message[:2000], now),
                )
                conn.commit()
            finally:
                conn.close()

    def list_notifications(self, limit) -> list[dict]:
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    def mark_notifications_read(self, notif_ids) -> None:
        if not notif_ids:
            return
        marks = ",".join("?" * len(notif_ids))
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    f"UPDATE notifications SET read=1 WHERE id IN ({marks})",
                    notif_ids,
                )
                conn.commit()
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------
class _PostgresBackend:
    """Supabase/Postgres store. Same API, survives the container.

    Connections come from a small pool: the app opens and closes these on every
    call the way it did with SQLite, and paying a TCP+TLS handshake each time
    against a remote database would be far more expensive than the query.
    """

    kind = "postgres"

    def __init__(self, dsn: str) -> None:
        # Imported here, not at module scope, so the SQLite default keeps
        # working on installs that never added the Postgres driver.
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        from psycopg_pool import ConnectionPool

        self._Jsonb = Jsonb
        self._dsn = dsn
        connect_timeout = float(os.getenv("SCRAPLING_DB_CONNECT_TIMEOUT", "10"))
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=int(os.getenv("SCRAPLING_DB_POOL_MAX", "8")),
            kwargs={"row_factory": dict_row},
            # A checkout that cannot be served should raise rather than stall a
            # request behind the 30s default.
            timeout=connect_timeout,
            open=False,
        )
        # wait=True so an unreachable or misconfigured database fails here, at
        # startup, with a clear error — rather than hanging the first query
        # minutes later. The caller turns this into a SQLite fallback.
        self._pool.open(wait=True, timeout=connect_timeout)

    def describe(self) -> str:
        # Never echo the DSN: it carries the database password.
        return f"postgres:{_PG_SCHEMA}"

    def _json(self, obj: Any):
        """Wrap a dict for a jsonb column, keeping `default=str` tolerance."""
        return self._Jsonb(obj, dumps=_encode)

    def init(self) -> None:
        """Create the schema if it is missing, then prove the store is usable.

        The tables are normally provisioned by migration, so this is a no-op on
        Supabase. It matters for a self-hosted Postgres pointed at an empty
        database, and it doubles as a startup connectivity check.
        """
        ddl = f"""
        CREATE SCHEMA IF NOT EXISTS {_PG_SCHEMA};

        CREATE TABLE IF NOT EXISTS {_PG_SCHEMA}.runs (
            id          text PRIMARY KEY,
            kind        text NOT NULL,
            label       text NOT NULL DEFAULT '',
            params      jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            status      text NOT NULL,
            error       text NOT NULL DEFAULT '',
            total       integer NOT NULL DEFAULT 0,
            ok          integer NOT NULL DEFAULT 0,
            blocked     integer NOT NULL DEFAULT 0,
            errors      integer NOT NULL DEFAULT 0,
            started_at  double precision NOT NULL,
            finished_at double precision,
            schedule_id text NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS {_PG_SCHEMA}.run_results (
            run_id text    NOT NULL
                   REFERENCES {_PG_SCHEMA}.runs (id) ON DELETE CASCADE,
            idx    integer NOT NULL,
            data   jsonb   NOT NULL,
            PRIMARY KEY (run_id, idx)
        );

        CREATE INDEX IF NOT EXISTS idx_runs_started
            ON {_PG_SCHEMA}.runs (started_at DESC);

        CREATE TABLE IF NOT EXISTS {_PG_SCHEMA}.schedules (
            id           text PRIMARY KEY,
            name         text NOT NULL,
            kind         text NOT NULL,
            params       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            interval_min integer NOT NULL CHECK (interval_min >= 1),
            enabled      boolean NOT NULL DEFAULT true,
            created_at   double precision NOT NULL,
            last_run_at  double precision,
            next_run_at  double precision NOT NULL,
            last_run_id  text NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_schedules_due
            ON {_PG_SCHEMA}.schedules (next_run_at) WHERE enabled;

        CREATE TABLE IF NOT EXISTS {_PG_SCHEMA}.jobs (
            id              text PRIMARY KEY,
            name            text NOT NULL,
            description     text NOT NULL DEFAULT '',
            kind            text NOT NULL,
            params          jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            options         jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            tags            jsonb NOT NULL DEFAULT '[]'::jsonb,
            pinned          boolean NOT NULL DEFAULT false,
            archived        boolean NOT NULL DEFAULT false,
            run_count       integer NOT NULL DEFAULT 0,
            last_run_id     text NOT NULL DEFAULT '',
            last_run_at     double precision,
            last_run_status text NOT NULL DEFAULT '',
            created_at      double precision NOT NULL,
            updated_at      double precision NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_updated
            ON {_PG_SCHEMA}.jobs (updated_at DESC);

        CREATE TABLE IF NOT EXISTS {_PG_SCHEMA}.audit_log (
            id          text PRIMARY KEY,
            actor       text NOT NULL DEFAULT '',
            action      text NOT NULL,
            object_kind text NOT NULL DEFAULT '',
            object_id   text NOT NULL DEFAULT '',
            detail      text NOT NULL DEFAULT '',
            created_at  double precision NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_audit_created
            ON {_PG_SCHEMA}.audit_log (created_at DESC);

        CREATE TABLE IF NOT EXISTS {_PG_SCHEMA}.notifications (
            id         text PRIMARY KEY,
            level      text NOT NULL,
            kind       text NOT NULL,
            message    text NOT NULL,
            read       boolean NOT NULL DEFAULT false,
            created_at double precision NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_notif_created
            ON {_PG_SCHEMA}.notifications (created_at DESC);

        CREATE TABLE IF NOT EXISTS {_PG_SCHEMA}.settings (
            key        text PRIMARY KEY,
            value      jsonb NOT NULL DEFAULT 'null'::jsonb,
            updated_at double precision NOT NULL
        );
        """
        with self._pool.connection() as conn:
            try:
                conn.execute(ddl)
            except Exception:
                # A least-privilege role may be allowed to read and write the
                # tables but not create them. That is fine as long as they are
                # already there, so fall back to a probe rather than refusing
                # to boot.
                conn.rollback()
                conn.execute(f"SELECT 1 FROM {_PG_SCHEMA}.runs LIMIT 1")
                _log.info("store: schema present, DDL skipped (insufficient rights)")

    # -- runs --------------------------------------------------------------
    def create_run(self, run_id, kind, params, label, schedule_id, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {_PG_SCHEMA}.runs"
                " (id, kind, label, params, status, started_at, schedule_id)"
                " VALUES (%s, %s, %s, %s, 'running', %s, %s)",
                (run_id, kind, label, self._json(params), now, schedule_id),
            )

    def finish_run(self, run_id, status, results, error, counts, now) -> None:
        ok, blocked, errors = counts
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE {_PG_SCHEMA}.runs SET status=%s, error=%s, total=%s, ok=%s,"
                " blocked=%s, errors=%s, finished_at=%s WHERE id=%s",
                (status, error[:500], len(results), ok, blocked, errors, now, run_id),
            )
            if results:
                # One pipelined round trip for the whole result set; a per-row
                # loop against a remote database is what makes a big run crawl.
                with conn.cursor() as cur:
                    cur.executemany(
                        f"INSERT INTO {_PG_SCHEMA}.run_results (run_id, idx, data)"
                        " VALUES (%s, %s, %s)"
                        " ON CONFLICT (run_id, idx) DO UPDATE SET data = EXCLUDED.data",
                        [(run_id, i, self._json(r)) for i, r in enumerate(results)],
                    )

    def list_runs(self, limit) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.runs ORDER BY started_at DESC LIMIT %s",
                (limit,),
            ).fetchall()

    def get_run(self, run_id) -> tuple[dict, list] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.runs WHERE id=%s", (run_id,)
            ).fetchone()
            if row is None:
                return None
            results = conn.execute(
                f"SELECT data FROM {_PG_SCHEMA}.run_results WHERE run_id=%s ORDER BY idx",
                (run_id,),
            ).fetchall()
        return dict(row), [r["data"] for r in results]

    def delete_run(self, run_id) -> None:
        # run_results goes with it via ON DELETE CASCADE.
        with self._pool.connection() as conn:
            conn.execute(f"DELETE FROM {_PG_SCHEMA}.runs WHERE id=%s", (run_id,))

    # -- schedules ---------------------------------------------------------
    def create_schedule(self, sched_id, name, kind, params, interval_min, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {_PG_SCHEMA}.schedules"
                " (id, name, kind, params, interval_min, enabled, created_at, next_run_at)"
                " VALUES (%s, %s, %s, %s, %s, true, %s, %s)",
                (sched_id, name, kind, self._json(params), interval_min,
                 now, now + interval_min * 60),
            )

    def list_schedules(self) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.schedules ORDER BY created_at DESC"
            ).fetchall()

    def get_schedule(self, sched_id) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.schedules WHERE id=%s", (sched_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def set_schedule_enabled(self, sched_id, enabled, next_run_at) -> None:
        with self._pool.connection() as conn:
            if enabled:
                conn.execute(
                    f"UPDATE {_PG_SCHEMA}.schedules SET enabled=true, next_run_at=%s"
                    " WHERE id=%s",
                    (next_run_at, sched_id),
                )
            else:
                conn.execute(
                    f"UPDATE {_PG_SCHEMA}.schedules SET enabled=false WHERE id=%s",
                    (sched_id,),
                )

    def delete_schedule(self, sched_id) -> None:
        with self._pool.connection() as conn:
            conn.execute(f"DELETE FROM {_PG_SCHEMA}.schedules WHERE id=%s", (sched_id,))

    def mark_schedule_ran(self, sched_id, run_id, now, next_run_at) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE {_PG_SCHEMA}.schedules SET last_run_at=%s, next_run_at=%s,"
                " last_run_id=%s WHERE id=%s",
                (now, next_run_at, run_id, sched_id),
            )

    def stats(self) -> dict[str, Any]:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT (SELECT COUNT(*) FROM {_PG_SCHEMA}.runs)        AS runs,"
                f"       (SELECT COUNT(*) FROM {_PG_SCHEMA}.run_results) AS results,"
                f"       (SELECT COUNT(*) FROM {_PG_SCHEMA}.schedules"
                "         WHERE enabled)                                 AS schedules,"
                f"       (SELECT COUNT(*) FROM {_PG_SCHEMA}.jobs)         AS saved_jobs,"
                f"       (SELECT COUNT(*) FROM {_PG_SCHEMA}.notifications"
                "         WHERE NOT read)                                AS unread"
            ).fetchone()
        return {
            "runs": row["runs"], "results": row["results"],
            "schedules": row["schedules"], "saved_jobs": row["saved_jobs"],
            "unread": row["unread"],
        }

    # -- settings -----------------------------------------------------------
    def set_setting(self, key, value, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {_PG_SCHEMA}.settings (key, value, updated_at)"
                " VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE"
                " SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                (key, self._json(value), now),
            )

    def get_setting(self, key):
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT value FROM {_PG_SCHEMA}.settings WHERE key=%s", (key,)
            ).fetchone()
        return dict(row["value"]) if row is not None else None

    # -- saved jobs ---------------------------------------------------------
    def list_jobs(self, include_archived) -> list[dict]:
        with self._pool.connection() as conn:
            if include_archived:
                return cast(list[dict], conn.execute(
                    f"SELECT * FROM {_PG_SCHEMA}.jobs"
                    " ORDER BY pinned DESC, updated_at DESC"
                ).fetchall())
            return cast(list[dict], conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.jobs WHERE archived = false"
                " ORDER BY pinned DESC, updated_at DESC"
            ).fetchall())

    def get_job(self, job_id) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.jobs WHERE id=%s", (job_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def create_job(self, job_id, name, description, kind, params, options, tags,
                   pinned, actor, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {_PG_SCHEMA}.jobs"
                " (id, name, description, kind, params, options, tags, pinned,"
                "  created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (job_id, name, description, kind, self._json(params), self._json(options),
                 self._json(tags), bool(pinned), now, now),
            )

    def update_job(self, job_id, fields, now) -> None:
        if not fields:
            return
        sets = []
        args = []
        for k, v in fields.items():
            sets.append(f"{k} = %s")
            args.append(self._json(v) if k in ("params", "options", "tags") else v)
        args += [now, job_id]
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE {_PG_SCHEMA}.jobs SET {', '.join(sets)}, updated_at=%s"
                " WHERE id=%s",
                args,
            )

    def touch_job(self, job_id, run_id, status, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE {_PG_SCHEMA}.jobs SET run_count = run_count + 1,"
                " last_run_id=%s, last_run_at=%s, last_run_status=%s, updated_at=%s"
                " WHERE id=%s",
                (run_id, now, status, now, job_id),
            )

    def delete_job(self, job_id) -> None:
        with self._pool.connection() as conn:
            conn.execute(f"DELETE FROM {_PG_SCHEMA}.jobs WHERE id=%s", (job_id,))

    # -- audit --------------------------------------------------------------
    def log_audit(self, audit_id, actor, action, object_kind, object_id, detail, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {_PG_SCHEMA}.audit_log"
                " (id, actor, action, object_kind, object_id, detail, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (audit_id, actor, action, object_kind, object_id, detail[:2000], now),
            )

    def list_audit(self, limit) -> list[dict]:
        with self._pool.connection() as conn:
            return cast(list[dict], conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.audit_log ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall())

    # -- notifications ------------------------------------------------------
    def add_notification(self, notif_id, level, kind, message, now) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {_PG_SCHEMA}.notifications (id, level, kind, message, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (notif_id, level, kind, message[:2000], now),
            )

    def list_notifications(self, limit) -> list[dict]:
        with self._pool.connection() as conn:
            return cast(list[dict], conn.execute(
                f"SELECT * FROM {_PG_SCHEMA}.notifications ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall())

    def mark_notifications_read(self, notif_ids) -> None:
        if not notif_ids:
            return
        marks = ", ".join(["%s"] * len(notif_ids))
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE {_PG_SCHEMA}.notifications SET read = true WHERE id IN ({marks})",
                notif_ids,
            )


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
def _select_backend():
    """Postgres when a DSN is configured, SQLite otherwise.

    A configured-but-broken Postgres falls back to SQLite rather than taking the
    whole app down: losing run history across restarts is bad, but refusing to
    scrape at all is worse.
    """
    dsn = _dsn()
    if not dsn:
        return _SqliteBackend()
    try:
        backend = _PostgresBackend(dsn)
        _log.info("store: using Postgres (schema %s)", _PG_SCHEMA)
        return backend
    except ImportError:
        _log.error(
            "store: a Postgres DSN is set but psycopg is not installed "
            "(pip install 'psycopg[binary,pool]'); falling back to SQLite"
        )
    except Exception as exc:  # pragma: no cover - depends on a live database
        _log.error("store: Postgres unavailable (%s); falling back to SQLite", exc)
    return _SqliteBackend()


_backend = _select_backend()

#: Which store is live — surfaced by /api/stats so the operator can tell at a
#: glance whether history will survive the next restart.
BACKEND = _backend.kind


def init() -> None:
    """Create the schema. Safe to call on every boot."""
    global _backend, BACKEND
    try:
        _backend.init()
    except Exception as exc:
        if _backend.kind != "postgres":
            raise
        _log.error("store: Postgres init failed (%s); falling back to SQLite", exc)
        _backend = _SqliteBackend()
        BACKEND = _backend.kind
        _backend.init()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def create_run(kind: str, params: dict, label: str = "", schedule_id: str = "") -> str:
    run_id = uuid.uuid4().hex[:12]
    _backend.create_run(run_id, kind, params, label, schedule_id, time.time())
    return run_id


def finish_run(run_id: str, status: str, results: list[dict], error: str = "") -> None:
    """Store the final result set and the counts the history list shows."""
    _backend.finish_run(
        run_id, status, results, error, _counts(results), time.time()
    )


def list_runs(limit: int = 60, offset: int = 0) -> list[dict]:
    out = []
    for row in _backend.list_runs(limit + offset):
        item = dict(row)
        item["params"] = _as_dict(item.get("params"))
        out.append(item)
    return out[offset:]


def get_run(run_id: str) -> dict | None:
    found = _backend.get_run(run_id)
    if found is None:
        return None
    row, results = found
    run = dict(row)
    run["params"] = _as_dict(run.get("params"))
    run["results"] = [_as_dict(r) for r in results]
    return run


def delete_run(run_id: str) -> None:
    _backend.delete_run(run_id)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
def create_schedule(name: str, kind: str, params: dict, interval_min: int) -> dict:
    sched_id = uuid.uuid4().hex[:12]
    # Floored at one minute: a zero interval would make the scheduler fire on
    # every tick, forever.
    interval = max(1, int(interval_min))
    _backend.create_schedule(sched_id, name, kind, params, interval, time.time())
    return get_schedule(sched_id) or {}


def list_schedules() -> list[dict]:
    out = []
    for row in _backend.list_schedules():
        item = dict(row)
        item["params"] = _as_dict(item.get("params"))
        item["enabled"] = bool(item["enabled"])
        out.append(item)
    return out


def get_schedule(sched_id: str) -> dict | None:
    row = _backend.get_schedule(sched_id)
    if row is None:
        return None
    item = dict(row)
    item["params"] = _as_dict(item.get("params"))
    item["enabled"] = bool(item["enabled"])
    return item


def set_schedule_enabled(sched_id: str, enabled: bool) -> None:
    # Re-enabling gives the schedule a minute of grace rather than firing it the
    # instant the next tick lands.
    _backend.set_schedule_enabled(sched_id, bool(enabled), time.time() + 60)


def delete_schedule(sched_id: str) -> None:
    _backend.delete_schedule(sched_id)


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
    _backend.mark_schedule_ran(
        sched_id, run_id, now, now + int(sched["interval_min"]) * 60
    )


def stats() -> dict[str, Any]:
    out = _backend.stats()
    out["backend"] = _backend.kind
    return out


# ---------------------------------------------------------------------------
# Application settings
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: dict[str, Any] = {
    "webhooks": [],            # list of URLs notified on job lifecycle events
    "notifications": True,     # in-app notifications on job start/finish
    "default_export": "csv",
    "default_mode": "http",
    "default_concurrency": 10,
    "default_timeout": 30,
    "default_retries": 3,
    "retention_days": 0,       # 0 = keep everything
}


def get_settings() -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        value = _backend.get_setting(key)
        if value is not None:
            out[key] = value
    return out


def set_setting(key: str, value: Any) -> None:
    if key not in DEFAULT_SETTINGS:
        raise ValueError(f"Unknown setting: {key}")
    _backend.set_setting(key, value, time.time())


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        set_setting(key, value)
    return get_settings()


# ---------------------------------------------------------------------------
# Saved jobs (reusable job configurations)
# ---------------------------------------------------------------------------
def list_jobs(include_archived: bool = False) -> list[dict]:
    out = []
    for row in _backend.list_jobs(include_archived):
        item = dict(row)
        item["params"] = _as_dict(item.get("params"))
        item["options"] = _as_dict(item.get("options"))
        tags = item.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except ValueError:
                tags = []
        item["tags"] = tags or []
        item["pinned"] = bool(item.get("pinned"))
        item["archived"] = bool(item.get("archived"))
        out.append(item)
    return out


def get_job(job_id: str) -> dict | None:
    row = _backend.get_job(job_id)
    if row is None:
        return None
    item = dict(row)
    item["params"] = _as_dict(item.get("params"))
    item["options"] = _as_dict(item.get("options"))
    tags = item.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = []
    item["tags"] = tags or []
    item["pinned"] = bool(item.get("pinned"))
    item["archived"] = bool(item.get("archived"))
    return item


def create_job(name, kind, params, options=None, description="", tags=None,
               pinned=False, actor="admin") -> dict:
    job_id = uuid.uuid4().hex[:12]
    _backend.create_job(
        job_id, name, description, kind,
        dict(params or {}), dict(options or {}), list(tags or []),
        bool(pinned), actor, time.time(),
    )
    return get_job(job_id) or {}


def update_job(job_id: str, fields: dict[str, Any]) -> dict | None:
    """Apply a partial update. JSON columns pass through homogenisation."""
    if get_job(job_id) is None:
        return None
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key in ("params", "options", "tags"):
            clean[key] = value
        elif key in ("name", "description", "kind"):
            clean[key] = value
        elif key in ("pinned", "archived"):
            clean[key] = int(bool(value))
    _backend.update_job(job_id, clean, time.time())
    return get_job(job_id)


def delete_job(job_id: str) -> None:
    _backend.delete_job(job_id)


def touch_job(job_id: str, run_id: str, status: str) -> None:
    _backend.touch_job(job_id, run_id, status, time.time())


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def log_audit(action: str, object_kind: str = "", object_id: str = "",
              detail: str = "", actor: str = "system") -> None:
    with _suppress(Exception):
        _backend.log_audit(
            uuid.uuid4().hex[:12], actor, action, object_kind, object_id,
            detail, time.time(),
        )


def list_audit(limit: int = 100) -> list[dict]:
    return [dict(r) for r in _backend.list_audit(limit)]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def add_notification(level: str, kind: str, message: str) -> None:
    _backend.add_notification(uuid.uuid4().hex[:12], level, kind, message, time.time())


def list_notifications(limit: int = 50) -> list[dict]:
    out = []
    for row in _backend.list_notifications(limit):
        item = dict(row)
        item["read"] = bool(item.get("read"))
        out.append(item)
    return out


def mark_notifications_read(notif_ids: list[str]) -> None:
    _backend.mark_notifications_read(list(notif_ids))


def notifications_summary() -> dict:
    unread = 0
    latest = list_notifications(10)
    # Re-count unread from the backend to stay cheap and correct.
    with _suppress(Exception):
        unread = int(_backend.stats().get("unread", 0))
    return {"unread": unread, "latest": latest}


# ---------------------------------------------------------------------------
# Dashboard aggregates — computed from real stored data.
# ---------------------------------------------------------------------------
def run_rows_for_window(days: int = 14) -> list[dict]:
    """The run-level rows a dashboard can aggregate; bounded to a window."""
    since = time.time() - days * 86400
    # list_runs returns fresh rows; a small extra pull keeps this self-contained.
    out = []
    for row in _backend.list_runs(10_000):
        item = dict(row)
        if float(item.get("started_at") or 0) >= since:
            out.append(item)
    return out


def dashboard() -> dict[str, Any]:
    """One payload for the command-centre dashboard.

    Everything here is derived from actual stored runs/schedules/jobs — no
    sample data, no fabricated metrics.
    """
    now = time.time()
    runs_all = [dict(r) for r in _backend.list_runs(10_000)]

    def _fin(window: float) -> list[dict]:
        return [r for r in runs_all if float(r.get("finished_at") or 0) >= now - window]

    runs_7d = _fin(7 * 86400)
    runs_1d = _fin(86400)
    finished = [r for r in runs_all if r.get("status") in ("done", "error", "cancelled")]

    ok_runs = [r for r in runs_all if r.get("status") == "done"]
    err_runs = [r for r in runs_all if r.get("status") == "error"]
    success_rate = round(100 * len(ok_runs) / len(finished), 1) if finished else None

    total_rows = sum(int(r.get("total") or 0) for r in runs_all)
    ok_rows = sum(int(r.get("ok") or 0) for r in runs_all)
    blocked_rows = sum(int(r.get("blocked") or 0) for r in runs_all)
    error_rows = sum(int(r.get("errors") or 0) for r in runs_all)

    # Needs attention: recent error runs plus schedules that errored last time.
    attention: list[dict] = []
    for r in sorted(runs_all, key=lambda x: x.get("finished_at") or 0, reverse=True):
        if r.get("status") == "error" and float(r.get("finished_at") or 0) >= now - 7 * 86400:
            attention.append({
                "id": r["id"], "kind": r.get("kind", ""), "label": r.get("label", ""),
                "status": "error", "error": r.get("error", ""),
                "finished_at": r.get("finished_at"),
            })
        if len(attention) >= 8:
            break
    schedules = list_schedules()
    for s in schedules:
        if s.get("enabled") and s.get("last_run_id"):
            run = next((r for r in runs_all if r["id"] == s["last_run_id"]), None)
            if run is not None and run.get("status") == "error":
                attention.append({
                    "id": s["id"], "kind": "schedule", "label": s.get("name", ""),
                    "status": "error", "error": run.get("error", ""),
                    "finished_at": run.get("finished_at"),
                })
    attention = attention[:10]

    return {
        "stats": {
            "total_runs": len(runs_all),
            "runs_7d": len(runs_7d),
            "runs_today": len(runs_1d),
            "total_rows": total_rows,
            "ok_rows": ok_rows,
            "blocked_rows": blocked_rows,
            "error_rows": error_rows,
            "success_rate": success_rate,
            "saved_jobs": len(list_jobs()),
            "active_schedules": sum(1 for s in schedules if s.get("enabled")),
            "error_runs": len(err_runs),
            "headers": [],
        },
        "timeseries": _runs_timeseries(runs_all, 14),
        "kinds": _runs_by_kind(runs_all),
        "attention": attention,
        "activity": _recent_activity(runs_all, schedules),
    }


def _runs_timeseries(runs: list[dict], days: int) -> list[dict]:
    """Bucketed per calendar day: runs, rows, ok/blocked/errored counts."""
    import datetime as _dt

    buckets: dict[Any, dict[str, Any]] = {}
    today = _dt.datetime.now().date()
    for offset in range(days, -1, -1):
        day = today - _dt.timedelta(days=offset)
        buckets[day] = {
            "label": day.isoformat(),
            "runs": 0, "rows": 0, "ok": 0, "blocked": 0, "errors": 0,
        }
    for r in runs:
        st = float(r.get("started_at") or 0)
        if not st:
            continue
        day = _dt.datetime.fromtimestamp(st).date()
        if day not in buckets:
            continue
        b = buckets[day]
        b["runs"] += 1
        b["rows"] += int(r.get("total") or 0)
        b["ok"] += int(r.get("ok") or 0)
        b["blocked"] += int(r.get("blocked") or 0)
        b["errors"] += int(r.get("errors") or 0)
    return list(buckets.values())


def _runs_by_kind(runs: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for r in runs:
        kind = r.get("kind") or "?"
        item = by.setdefault(kind, {"kind": kind, "runs": 0, "rows": 0, "errors": 0})
        item["runs"] += 1
        item["rows"] += int(r.get("total") or 0)
        item["errors"] += int(r.get("errors") or 0)
    return sorted(by.values(), key=lambda k: k["runs"], reverse=True)


def _recent_activity(runs: list[dict], schedules: list[dict]) -> list[dict]:
    """Ordered latest-first mix of finished runs and schedule activity."""
    events: list[dict] = []
    for r in runs:
        ts = r.get("finished_at") or r.get("started_at") or 0
        events.append({
            "ts": ts, "kind": r.get("kind", ""), "label": r.get("label", ""),
            "status": r.get("status", ""), "id": r.get("id", ""),
            "detail": f"{r.get('ok', 0)} ok · {r.get('blocked', 0)} blocked · {r.get('errors', 0)} err",
        })
    for s in schedules:
        latest = None
        for r in runs:
            if r.get("id") == s.get("last_run_id"):
                latest = r
                break
        ts = (latest or {}).get("finished_at") or s.get("last_run_at") or s.get("created_at") or 0
        status = (latest or {}).get("status", "") if latest else ("schedule" if s.get("enabled") else "paused")
        events.append({
            "ts": ts, "kind": "schedule", "label": s.get("name", ""),
            "status": status, "id": s.get("id", ""),
        })
    events.sort(key=lambda e: float(e["ts"] or 0), reverse=True)
    return events[:20]
