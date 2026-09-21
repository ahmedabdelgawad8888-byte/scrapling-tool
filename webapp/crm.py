"""The layer that turns individual runs into a durable creator database.

``store`` answers "what did the operator run and what came back". This module
answers "who have we ever found, and what do we know about them now" — which is
a different shape: a creator seen in eight runs is one row here, carrying the
best value for each field and a history of how their audience moved.

It also holds the operational tables the dashboard needs to be self-serve:
provider credentials, the proxy pool, and per-provider call telemetry.

Deliberately SQLite-only, sharing ``store``'s database file. The Postgres
backend in ``store`` exists so run history survives an ephemeral container
filesystem; these tables belong to a local operator install, where that concern
does not apply. Keeping them on one engine avoids maintaining two dialects of
every query for a case nobody is running.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any

from webapp.store import DB_PATH

_log = logging.getLogger(__name__)
_LOCK = threading.Lock()

# Fields we keep denormalised on the creator row because every list view sorts
# or filters by them. Everything else lives in the `data` JSON blob.
SORTABLE = (
    "last_seen", "first_seen", "followers", "engagement_rate", "quality",
    "username", "platform", "seen_count",
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _encode(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _decode(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def creator_id(platform: str, username: str) -> str:
    """Stable identity for a creator.

    Platform plus lowercased handle. Not the profile URL: the same account is
    reachable at ``/@name``, ``/name/`` and with tracking parameters, and three
    URLs for one person would defeat the whole point of this table.
    """
    return f"{(platform or 'unknown').strip().lower()}:{(username or '').strip().lower().lstrip('@')}"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    username        TEXT NOT NULL,
    full_name       TEXT NOT NULL DEFAULT '',
    profile_url     TEXT NOT NULL DEFAULT '',
    avatar_url      TEXT NOT NULL DEFAULT '',
    biography       TEXT NOT NULL DEFAULT '',
    followers       INTEGER NOT NULL DEFAULT 0,
    following       INTEGER NOT NULL DEFAULT 0,
    likes           INTEGER NOT NULL DEFAULT 0,
    avg_views       INTEGER NOT NULL DEFAULT 0,
    engagement_rate REAL    NOT NULL DEFAULT 0,
    quality         INTEGER NOT NULL DEFAULT 0,
    is_verified     INTEGER NOT NULL DEFAULT 0,
    is_private      INTEGER NOT NULL DEFAULT 0,
    country         TEXT NOT NULL DEFAULT '',
    city            TEXT NOT NULL DEFAULT '',
    emails          TEXT NOT NULL DEFAULT '[]',
    phones          TEXT NOT NULL DEFAULT '[]',
    links           TEXT NOT NULL DEFAULT '[]',
    notes           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'new',
    data            TEXT NOT NULL DEFAULT '{}',
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL,
    seen_count      INTEGER NOT NULL DEFAULT 1,
    last_run_id     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_creators_seen      ON creators (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_creators_platform  ON creators (platform);
CREATE INDEX IF NOT EXISTS idx_creators_followers ON creators (followers DESC);
CREATE INDEX IF NOT EXISTS idx_creators_quality   ON creators (quality DESC);

CREATE TABLE IF NOT EXISTS creator_tags (
    creator_id TEXT NOT NULL,
    tag        TEXT NOT NULL,
    PRIMARY KEY (creator_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_tags_tag ON creator_tags (tag);

-- One row per observation, so the Analytics page can show how an audience moved
-- between two runs of the same profile. Written only when something changed.
CREATE TABLE IF NOT EXISTS creator_history (
    creator_id TEXT NOT NULL,
    seen_at    REAL NOT NULL,
    followers  INTEGER NOT NULL DEFAULT 0,
    following  INTEGER NOT NULL DEFAULT 0,
    likes      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (creator_id, seen_at)
);

CREATE TABLE IF NOT EXISTS lists (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT 'slate',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS list_members (
    list_id    TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    added_at   REAL NOT NULL,
    PRIMARY KEY (list_id, creator_id)
);

CREATE INDEX IF NOT EXISTS idx_members_creator ON list_members (creator_id);

CREATE TABLE IF NOT EXISTS proxies (
    id           TEXT PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'unknown',
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    ok_count     INTEGER NOT NULL DEFAULT 0,
    fail_count   INTEGER NOT NULL DEFAULT 0,
    last_checked REAL,
    last_error   TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_keys (
    provider    TEXT PRIMARY KEY,
    api_key     TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    order_index INTEGER NOT NULL DEFAULT 100,
    updated_at  REAL NOT NULL
);

-- Rolled up per day rather than per call: the dashboard only ever plots trends,
-- and an unbounded call log on a scraper that makes thousands of requests an
-- hour would grow faster than anything else in the database.
CREATE TABLE IF NOT EXISTS provider_stats (
    provider  TEXT NOT NULL,
    day       TEXT NOT NULL,
    calls     INTEGER NOT NULL DEFAULT 0,
    ok        INTEGER NOT NULL DEFAULT 0,
    errors    INTEGER NOT NULL DEFAULT 0,
    total_ms  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, day)
);

-- Same rollup for fetch outcomes, keyed by domain and mode. This is what makes
-- "Instagram blocks us in http mode but not stealth" a visible fact instead of
-- a thing the operator has to remember.
CREATE TABLE IF NOT EXISTS fetch_stats (
    domain   TEXT NOT NULL,
    mode     TEXT NOT NULL,
    day      TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    ok       INTEGER NOT NULL DEFAULT 0,
    blocked  INTEGER NOT NULL DEFAULT 0,
    errors   INTEGER NOT NULL DEFAULT 0,
    total_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (domain, mode, day)
);
"""


def init() -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Creator ingestion
# ---------------------------------------------------------------------------
def _row_to_creator(row: dict) -> dict | None:
    """Map one enriched scrape result onto creator columns, or None to skip."""
    platform = str(row.get("platform") or "").strip().lower()
    username = str(row.get("username") or "").strip().lstrip("@")
    if not username:
        return None
    if not platform:
        platform = "unknown"

    return {
        "id": creator_id(platform, username),
        "platform": platform,
        "username": username,
        "full_name": str(row.get("full_name") or "")[:300],
        "profile_url": str(row.get("profile_url") or row.get("url") or "")[:1000],
        "avatar_url": str(row.get("avatar_url") or row.get("avatar") or "")[:1000],
        "biography": str(row.get("biography") or row.get("bio") or "")[:4000],
        "followers": int(row.get("followers_n") or 0),
        "following": int(row.get("following_n") or 0),
        "likes": int(row.get("likes_n") or 0),
        "avg_views": int(row.get("avg_views_n") or 0),
        "engagement_rate": float(row.get("engagement_rate") or 0.0),
        "quality": int(row.get("quality") or 0),
        "is_verified": int(bool(row.get("is_verified"))),
        "is_private": int(bool(row.get("is_private"))),
        "country": str(row.get("country") or "")[:100],
        "city": str(row.get("city") or "")[:100],
        "emails": _encode(row.get("emails") or []),
        "phones": _encode(row.get("phones") or []),
        "links": _encode(row.get("links") or []),
    }


# Counters only ever grow in reality, so a smaller reading is a parse failure or
# a partially hydrated page rather than real decline. Taking the max stops one
# bad scrape from wiping a good value; text fields take the longest non-empty
# string for the same reason.
_MAX_FIELDS = ("followers", "following", "likes", "avg_views", "quality")
_LONGEST_FIELDS = ("full_name", "biography", "profile_url", "avatar_url", "country", "city")


def upsert_creators(rows: list[dict], run_id: str = "") -> dict[str, int]:
    """Fold a run's results into the creator table.

    Returns ``{"added": n, "updated": n, "skipped": n}``.
    """
    from scrapling_tool.enrich import enrich_row

    now = time.time()
    added = updated = skipped = 0

    with _LOCK:
        conn = _connect()
        try:
            for raw in rows:
                if not isinstance(raw, dict):
                    skipped += 1
                    continue
                # A blocked or errored fetch carries no facts worth storing.
                if raw.get("blocked") or raw.get("error"):
                    skipped += 1
                    continue

                incoming = _row_to_creator(enrich_row(raw))
                if incoming is None:
                    skipped += 1
                    continue

                existing = conn.execute(
                    "SELECT * FROM creators WHERE id=?", (incoming["id"],)
                ).fetchone()

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO creators
                            (id, platform, username, full_name, profile_url, avatar_url,
                             biography, followers, following, likes, avg_views,
                             engagement_rate, quality, is_verified, is_private,
                             country, city, emails, phones, links,
                             first_seen, last_seen, seen_count, last_run_id)
                        VALUES
                            (:id, :platform, :username, :full_name, :profile_url, :avatar_url,
                             :biography, :followers, :following, :likes, :avg_views,
                             :engagement_rate, :quality, :is_verified, :is_private,
                             :country, :city, :emails, :phones, :links,
                             :now, :now, 1, :run_id)
                        """,
                        {**incoming, "now": now, "run_id": run_id},
                    )
                    added += 1
                else:
                    merged = dict(incoming)

                    for field in _MAX_FIELDS:
                        merged[field] = max(incoming[field], existing[field] or 0)
                    for field in _LONGEST_FIELDS:
                        if len(str(existing[field] or "")) > len(str(incoming[field] or "")):
                            merged[field] = existing[field]

                    merged["engagement_rate"] = (
                        incoming["engagement_rate"] or existing["engagement_rate"] or 0.0
                    )
                    # Contacts accumulate: an email found once stays known even
                    # if a later scrape came back with an empty bio.
                    for field in ("emails", "phones"):
                        old = _decode(existing[field], [])
                        new = _decode(incoming[field], [])
                        merged[field] = _encode(list(dict.fromkeys([*old, *new])))
                    old_links = {li.get("url"): li for li in _decode(existing["links"], [])}
                    for li in _decode(incoming["links"], []):
                        old_links[li.get("url")] = li
                    merged["links"] = _encode([li for li in old_links.values() if li])

                    conn.execute(
                        """
                        UPDATE creators SET
                            platform=:platform, username=:username, full_name=:full_name,
                            profile_url=:profile_url, avatar_url=:avatar_url,
                            biography=:biography, followers=:followers, following=:following,
                            likes=:likes, avg_views=:avg_views,
                            engagement_rate=:engagement_rate, quality=:quality,
                            is_verified=:is_verified, is_private=:is_private,
                            country=:country, city=:city,
                            emails=:emails, phones=:phones, links=:links,
                            last_seen=:now, seen_count=seen_count+1, last_run_id=:run_id
                        WHERE id=:id
                        """,
                        {**merged, "now": now, "run_id": run_id},
                    )
                    updated += 1

                # History point, but only when the audience actually moved —
                # re-running the same list hourly should not write a row an hour.
                last = conn.execute(
                    "SELECT followers FROM creator_history WHERE creator_id=?"
                    " ORDER BY seen_at DESC LIMIT 1",
                    (incoming["id"],),
                ).fetchone()
                if last is None or last["followers"] != incoming["followers"]:
                    conn.execute(
                        "INSERT OR REPLACE INTO creator_history"
                        " (creator_id, seen_at, followers, following, likes)"
                        " VALUES (?,?,?,?,?)",
                        (
                            incoming["id"], now, incoming["followers"],
                            incoming["following"], incoming["likes"],
                        ),
                    )

            conn.commit()
        finally:
            conn.close()

    return {"added": added, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Creator queries
# ---------------------------------------------------------------------------
def _hydrate(row: sqlite3.Row, tags: list[str] | None = None) -> dict:
    out = dict(row)
    out["emails"] = _decode(out.get("emails"), [])
    out["phones"] = _decode(out.get("phones"), [])
    out["links"] = _decode(out.get("links"), [])
    out["data"] = _decode(out.get("data"), {})
    out["is_verified"] = bool(out.get("is_verified"))
    out["is_private"] = bool(out.get("is_private"))
    out["tags"] = tags or []
    return out


def search_creators(
    *,
    query: str = "",
    platform: str = "",
    tag: str = "",
    list_id: str = "",
    min_followers: int = 0,
    max_followers: int = 0,
    min_quality: int = 0,
    has_contact: bool = False,
    verified_only: bool = False,
    sort: str = "last_seen",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Filtered, sorted page of creators plus the total matching count."""
    where: list[str] = []
    params: list[Any] = []

    if query:
        where.append(
            "(c.username LIKE ? OR c.full_name LIKE ? OR c.biography LIKE ?"
            " OR c.emails LIKE ?)"
        )
        like = f"%{query}%"
        params += [like, like, like, like]
    if platform:
        where.append("c.platform = ?")
        params.append(platform.lower())
    if min_followers > 0:
        where.append("c.followers >= ?")
        params.append(min_followers)
    if max_followers > 0:
        where.append("c.followers <= ?")
        params.append(max_followers)
    if min_quality > 0:
        where.append("c.quality >= ?")
        params.append(min_quality)
    if has_contact:
        where.append("(c.emails != '[]' OR c.phones != '[]')")
    if verified_only:
        where.append("c.is_verified = 1")
    if tag:
        where.append("EXISTS (SELECT 1 FROM creator_tags t WHERE t.creator_id=c.id AND t.tag=?)")
        params.append(tag)
    if list_id:
        where.append("EXISTS (SELECT 1 FROM list_members m WHERE m.creator_id=c.id AND m.list_id=?)")
        params.append(list_id)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    sort_col = sort if sort in SORTABLE else "last_seen"
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    limit = max(1, min(int(limit or 100), 1000))

    conn = _connect()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM creators c {clause}", params
        ).fetchone()["n"]

        rows = conn.execute(
            f"SELECT c.* FROM creators c {clause}"
            f" ORDER BY c.{sort_col} {direction}, c.id ASC LIMIT ? OFFSET ?",
            [*params, limit, max(0, int(offset or 0))],
        ).fetchall()

        # One query for every tag on the page beats one query per row.
        ids = [r["id"] for r in rows]
        tag_map: dict[str, list[str]] = {i: [] for i in ids}
        if ids:
            marks = ",".join("?" * len(ids))
            for tr in conn.execute(
                f"SELECT creator_id, tag FROM creator_tags WHERE creator_id IN ({marks})",
                ids,
            ):
                tag_map.setdefault(tr["creator_id"], []).append(tr["tag"])

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_hydrate(r, tag_map.get(r["id"], [])) for r in rows],
        }
    finally:
        conn.close()


def get_creator(cid: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM creators WHERE id=?", (cid,)).fetchone()
        if row is None:
            return None
        tags = [r["tag"] for r in conn.execute(
            "SELECT tag FROM creator_tags WHERE creator_id=? ORDER BY tag", (cid,)
        )]
        out = _hydrate(row, tags)
        out["history"] = [dict(r) for r in conn.execute(
            "SELECT seen_at, followers, following, likes FROM creator_history"
            " WHERE creator_id=? ORDER BY seen_at ASC",
            (cid,),
        )]
        out["lists"] = [dict(r) for r in conn.execute(
            "SELECT l.id, l.name, l.color FROM lists l"
            " JOIN list_members m ON m.list_id=l.id WHERE m.creator_id=?",
            (cid,),
        )]
        return out
    finally:
        conn.close()


def delete_creators(ids: list[str]) -> int:
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(f"DELETE FROM creators WHERE id IN ({marks})", ids)
            conn.execute(f"DELETE FROM creator_tags WHERE creator_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM list_members WHERE creator_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM creator_history WHERE creator_id IN ({marks})", ids)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def update_creator(cid: str, *, notes: str | None = None, status: str | None = None) -> bool:
    sets, params = [], []
    if notes is not None:
        sets.append("notes=?")
        params.append(notes[:5000])
    if status is not None:
        sets.append("status=?")
        params.append(status[:40])
    if not sets:
        return False

    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                f"UPDATE creators SET {', '.join(sets)} WHERE id=?", [*params, cid]
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def add_tags(ids: list[str], tags: list[str]) -> int:
    clean = [t.strip().lower()[:40] for t in tags if t and t.strip()]
    if not ids or not clean:
        return 0
    with _LOCK:
        conn = _connect()
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO creator_tags (creator_id, tag) VALUES (?,?)",
                [(cid, tag) for cid in ids for tag in clean],
            )
            conn.commit()
            return len(ids) * len(clean)
        finally:
            conn.close()


def remove_tags(ids: list[str], tags: list[str]) -> int:
    clean = [t.strip().lower() for t in tags if t and t.strip()]
    if not ids or not clean:
        return 0
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.executemany(
                "DELETE FROM creator_tags WHERE creator_id=? AND tag=?",
                [(cid, tag) for cid in ids for tag in clean],
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def all_tags() -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT tag, COUNT(*) AS n FROM creator_tags GROUP BY tag ORDER BY n DESC, tag"
        )]
    finally:
        conn.close()


def platforms() -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT platform, COUNT(*) AS n FROM creators GROUP BY platform ORDER BY n DESC"
        )]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------
def create_list(name: str, description: str = "", color: str = "slate") -> dict:
    now = time.time()
    lid = uuid.uuid4().hex[:12]
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO lists (id, name, description, color, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (lid, name.strip()[:120] or "Untitled list", description[:1000], color, now, now),
            )
            conn.commit()
        finally:
            conn.close()
    return {"id": lid, "name": name, "description": description, "color": color,
            "created_at": now, "updated_at": now, "count": 0}


def list_lists() -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT l.*, (SELECT COUNT(*) FROM list_members m WHERE m.list_id=l.id) AS count"
            " FROM lists l ORDER BY l.updated_at DESC"
        )]
    finally:
        conn.close()


def update_list(lid: str, *, name: str | None = None, description: str | None = None,
                color: str | None = None) -> bool:
    sets, params = [], []
    if name is not None:
        sets.append("name=?")
        params.append(name.strip()[:120])
    if description is not None:
        sets.append("description=?")
        params.append(description[:1000])
    if color is not None:
        sets.append("color=?")
        params.append(color[:20])
    if not sets:
        return False
    sets.append("updated_at=?")
    params.append(time.time())

    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(f"UPDATE lists SET {', '.join(sets)} WHERE id=?", [*params, lid])
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def delete_list(lid: str) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM lists WHERE id=?", (lid,))
            conn.execute("DELETE FROM list_members WHERE list_id=?", (lid,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def add_to_list(lid: str, ids: list[str]) -> int:
    if not ids:
        return 0
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO list_members (list_id, creator_id, added_at)"
                " VALUES (?,?,?)",
                [(lid, cid, now) for cid in ids],
            )
            conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (now, lid))
            conn.commit()
        finally:
            conn.close()
    return len(ids)


def remove_from_list(lid: str, ids: list[str]) -> int:
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                f"DELETE FROM list_members WHERE list_id=? AND creator_id IN ({marks})",
                [lid, *ids],
            )
            conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (time.time(), lid))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------
def add_proxy(url: str, label: str = "") -> dict | None:
    url = url.strip()
    if not url:
        return None
    pid = uuid.uuid4().hex[:12]
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO proxies (id, url, label, created_at) VALUES (?,?,?,?)",
                (pid, url, label[:100], now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM proxies WHERE url=?", (url,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_proxies(enabled_only: bool = False) -> list[dict]:
    conn = _connect()
    try:
        sql = "SELECT * FROM proxies"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY enabled DESC, status='ok' DESC, latency_ms ASC, created_at ASC"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def set_proxy_enabled(pid: str, enabled: bool) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE proxies SET enabled=? WHERE id=?", (int(enabled), pid)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def delete_proxy(pid: str) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM proxies WHERE id=?", (pid,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def record_proxy_check(pid: str, ok: bool, latency_ms: int, error: str = "") -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE proxies SET status=?, latency_ms=?, last_checked=?, last_error=?,"
                " ok_count=ok_count+?, fail_count=fail_count+? WHERE id=?",
                (
                    "ok" if ok else "down", int(latency_ms), time.time(), error[:500],
                    1 if ok else 0, 0 if ok else 1, pid,
                ),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Provider credentials and telemetry
# ---------------------------------------------------------------------------
def set_provider_key(provider: str, api_key: str, enabled: bool = True,
                     order_index: int = 100) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO provider_keys (provider, api_key, enabled, order_index, updated_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(provider) DO UPDATE SET"
                "   api_key=excluded.api_key, enabled=excluded.enabled,"
                "   order_index=excluded.order_index, updated_at=excluded.updated_at",
                (provider, api_key.strip(), int(enabled), int(order_index), time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def get_provider_keys() -> dict[str, dict]:
    """Full records including the secret. Server-side callers only."""
    conn = _connect()
    try:
        return {r["provider"]: dict(r) for r in conn.execute("SELECT * FROM provider_keys")}
    finally:
        conn.close()


def delete_provider_key(provider: str) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM provider_keys WHERE provider=?", (provider,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def record_provider_call(provider: str, ok: bool, elapsed_ms: int) -> None:
    day = time.strftime("%Y-%m-%d")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO provider_stats (provider, day, calls, ok, errors, total_ms)"
                " VALUES (?,?,1,?,?,?)"
                " ON CONFLICT(provider, day) DO UPDATE SET"
                "   calls=calls+1, ok=ok+excluded.ok, errors=errors+excluded.errors,"
                "   total_ms=total_ms+excluded.total_ms",
                (provider, day, 1 if ok else 0, 0 if ok else 1, max(0, int(elapsed_ms))),
            )
            conn.commit()
        finally:
            conn.close()


def provider_stats(days: int = 14) -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT provider, day, calls, ok, errors, total_ms FROM provider_stats"
            " WHERE day >= date('now', ?) ORDER BY day ASC, provider ASC",
            (f"-{max(1, int(days))} days",),
        )]
    finally:
        conn.close()


def record_fetch(domain: str, mode: str, outcome: str, elapsed_ms: int) -> None:
    """Log one fetch attempt. ``outcome`` is ok | blocked | error."""
    day = time.strftime("%Y-%m-%d")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO fetch_stats (domain, mode, day, attempts, ok, blocked, errors, total_ms)"
                " VALUES (?,?,?,1,?,?,?,?)"
                " ON CONFLICT(domain, mode, day) DO UPDATE SET"
                "   attempts=attempts+1, ok=ok+excluded.ok, blocked=blocked+excluded.blocked,"
                "   errors=errors+excluded.errors, total_ms=total_ms+excluded.total_ms",
                (
                    domain[:200], mode[:20], day,
                    1 if outcome == "ok" else 0,
                    1 if outcome == "blocked" else 0,
                    1 if outcome == "error" else 0,
                    max(0, int(elapsed_ms)),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def fetch_stats(days: int = 14) -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT domain, mode, day, attempts, ok, blocked, errors, total_ms"
            " FROM fetch_stats WHERE day >= date('now', ?)"
            " ORDER BY attempts DESC",
            (f"-{max(1, int(days))} days",),
        )]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aggregates for the Analytics page
# ---------------------------------------------------------------------------
def creator_summary() -> dict[str, Any]:
    conn = _connect()
    try:
        base = conn.execute(
            "SELECT COUNT(*) AS total,"
            "       SUM(CASE WHEN emails != '[]' OR phones != '[]' THEN 1 ELSE 0 END) AS reachable,"
            "       SUM(CASE WHEN is_verified=1 THEN 1 ELSE 0 END) AS verified,"
            "       AVG(engagement_rate) AS avg_engagement,"
            "       AVG(quality) AS avg_quality"
            " FROM creators"
        ).fetchone()

        # Buckets rather than a raw histogram: the tiers are how influencer work
        # is actually priced, so they mean something to the operator.
        bands = conn.execute(
            "SELECT CASE"
            "         WHEN followers >= 1000000 THEN 'mega'"
            "         WHEN followers >= 100000  THEN 'macro'"
            "         WHEN followers >= 10000   THEN 'mid'"
            "         WHEN followers >= 1000    THEN 'micro'"
            "         ELSE 'nano' END AS band,"
            "       COUNT(*) AS n"
            " FROM creators GROUP BY band"
        ).fetchall()

        movers = conn.execute(
            "SELECT h.creator_id, c.username, c.platform, c.followers,"
            "       MIN(h.followers) AS low, MAX(h.followers) AS high,"
            "       COUNT(*) AS points"
            " FROM creator_history h JOIN creators c ON c.id = h.creator_id"
            " GROUP BY h.creator_id HAVING points > 1 AND high > low"
            " ORDER BY (high - low) DESC LIMIT 10"
        ).fetchall()

        return {
            "total": base["total"] or 0,
            "reachable": base["reachable"] or 0,
            "verified": base["verified"] or 0,
            "avg_engagement": round(base["avg_engagement"] or 0.0, 2),
            "avg_quality": round(base["avg_quality"] or 0.0, 1),
            "bands": {r["band"]: r["n"] for r in bands},
            "movers": [
                {
                    "id": r["creator_id"], "username": r["username"],
                    "platform": r["platform"], "followers": r["followers"],
                    "gained": r["high"] - r["low"],
                }
                for r in movers
            ],
            "platforms": platforms(),
            "tags": all_tags()[:20],
        }
    finally:
        conn.close()
