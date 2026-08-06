#!/usr/bin/env python3
"""
ULTRA SCRAPER - The Ultimate Scrapling Agent
=============================================
Exposes ALL features of Scrapling v0.4.8 through a unified CLI.

Fetcher Modes (ascending protection):
  1. http      - AsyncFetcher (curl_cffi impersonation)
  2. session   - FetcherSession (persistent connection pool)
  3. browser   - DynamicFetcher (Playwright browser)
  4. stealth   - StealthyFetcher (anti-detection + Cloudflare solver)

Features:
  - HTTP methods: GET, POST, PUT, DELETE
  - Browser fetch with full JS rendering
  - Anti-detection: browser impersonation, stealth headers, canvas noise,
    WebRTC blocking, WebGL control, Cloudflare solving
  - Real Chrome support (use your installed Chrome)
  - Session persistence across requests
  - Bulk batch scraping with concurrency control
  - Spider/crawler with URL dedup, domain filtering, checkpoint/resume
  - Proxy support with auto rotation
  - Element extraction via CSS/XPath selectors
  - AI-targeted content sanitization
  - Screenshot capture (full page / viewport)
  - Multiple output formats: HTML, Markdown, Text, JSON
  - Instagram-aware profile parsing
  - Interactive IPython shell
  - MCP AI server mode
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import html
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse as _urlparse

import click

from scrapling_tool.discovery import (
    SUPPORTED_PLATFORMS,
    derive_seed_keywords,
    similarity_breakdown,
    split_keywords,
)
from scrapling_tool.mention_intelligence import (
    CONFIDENCE_TIERS,
    aggregate_creators,
    apply_evidence_policy,
    expand_brand_aliases,
    normalize_recency,
)
from scrapling_tool.parsers import extract_profile

# Compiled regex patterns for speed
_RE_TIKTOK = re.compile(r"/@([\w.\-]+)(?:/video/(\d+))?")
_RE_TIKTOK_TAG = re.compile(r"/tag/([^/?#]+)")
_RE_INSTAGRAM = re.compile(r"/(p|reel|reels|explore/tags)/([^/?#]+)")
_RE_INSTAGRAM_USER = re.compile(r"^/?([\w.\-]+)/?$")
_RE_SNAPCHAT = re.compile(r"/add/([\w.\-]+)")
_RE_YOUTUBE_CHANNEL = re.compile(r"^/(@[\w.\-]+|channel/[\w\-]+|c/[\w.\-]+|user/[\w.\-]+|shorts/[\w\-]+|hashtag/[^/?#]+)")
_RE_TWITTER = re.compile(r"^/([A-Za-z0-9_]+)(?:/status/(\d+))?")
_RE_LOGIN_WALL = re.compile(r'"loginAndSignupPage"|href="/accounts/login/"|action="/accounts/login/"|"login_page_v[eo]"')
_RE_JSON_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)
_RE_SHARED_DATA = re.compile(r"window\.__sharedData\s*=\s*({.*?});", re.DOTALL)
_RE_ADDITIONAL_DATA = re.compile(r'<script[^>]*type="application/json"[^>]*data-sly-repeat[^>]*>(.*?)</script>', re.DOTALL)
_RE_OG_DESC_STATS = re.compile(r"([\d.,]+[KMBkmb]?)\s*(Posts|Followers|Following)")
_RE_EXTERNAL_URL = re.compile(r'"external_url"\s*:\s*"([^"]+)"')
_RE_SEARCH_REDIRECT = re.compile(r"^(m|mobile|l|vm|www)\.")
_RE_URL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_RE_CLEAN_URL = re.compile(r"^[a-z][a-z0-9+.-]*://")
_RE_PRIVATE_HOST = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|::1)$")
_RE_PRIVATE_PREFIX = re.compile(r"^(10\.|192\.168\.|172\.)")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_logger = logging.getLogger("ultra_scraper")
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Lazy Scrapling imports (handles missing deps gracefully)
# ---------------------------------------------------------------------------
_HAS_FETCHERS = False
try:
    from scrapling import (
        AsyncFetcher,
        DynamicFetcher,
        Fetcher,
        Selector,
        Selectors,
        StealthyFetcher,
    )
    from scrapling.fetchers import AsyncStealthySession, FetcherSession, ProxyRotator
    _HAS_FETCHERS = True
except Exception as _import_err:
    _HAS_FETCHERS = False
    _HAS_IMPORT_ERR = str(_import_err)
    # Placeholders when Scrapling is not installed
    AsyncFetcher = None
    Fetcher = None
    DynamicFetcher = None
    StealthyFetcher = None
    Selector = None
    Selectors = None
    FetcherSession = None
    ProxyRotator = None
    AsyncStealthySession = None

_HAS_PLAYWRIGHT = False
try:
    from playwright.async_api import async_playwright
    _HAS_PLAYWRIGHT = True
except Exception:
    pass

# Optional deps below are imported with a broad `except` on purpose: a broken
# transitive dependency (e.g. a pydantic/pydantic-core version mismatch in the
# MCP stack) raises SystemError, not ImportError. Catching only ImportError
# would let that kill the entire CLI — and the web server the panel depends on.
_HAS_SHELL = False
try:
    from scrapling.core.shell import CustomShell
    _HAS_SHELL = True
except Exception:
    pass

_HAS_MCP = False
try:
    from scrapling.core.ai import ScraplingMCPServer
    _HAS_MCP = True
except Exception:
    pass

_HAS_RICH = False
try:
    from rich.console import Console
    from rich.live import Live
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    _HAS_RICH = True
except Exception:
    Console = None

console = Console(stderr=True) if _HAS_RICH else None

# ---------------------------------------------------------------------------
# Search provider API keys (from environment)
# ---------------------------------------------------------------------------
_QUERIT_API_KEY = os.environ.get("QUERIT_API_KEY", "")
_TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
_SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


def _echo(msg: str, color: str = "white"):
    if console:
        console.print(f"[{color}]{msg}[/]")
    else:
        click.echo(msg)


def _read_urls(source: str) -> list[str]:
    """Read URLs from a file or string."""
    path = Path(source)
    if path.exists():
        raw = path.read_text(encoding="utf-8")
    else:
        raw = source
    return [
        line.strip()
        for line in raw.strip().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _username_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1].split("?")[0]


def _platform_of(url: str) -> str:
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "instagram.com" in u:
        return "instagram"
    if "snapchat.com" in u:
        return "snapchat"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "twitter.com" in u or re.search(r"https?://(www\.)?x\.com", u):
        return "twitter"
    return "other"


@lru_cache(maxsize=2048)
def _canonical_social_url(raw_url: str) -> str:
    """Best-effort canonical social URL cleanup for pasted/search-result links."""
    from urllib.parse import parse_qs, unquote, urlparse, urlunparse

    raw = (raw_url or "").strip().strip(".,;)'\"<>[]{}")
    if not raw:
        return ""
    if not _RE_URL_SCHEME.match(raw):
        raw = "https://" + raw.lstrip("/")

    try:
        parsed = urlparse(raw)
    except Exception:
        return raw_url.strip()

    # Unwrap common search redirect URLs before platform detection.
    qs = parse_qs(parsed.query)
    for key in ("url", "u", "q", "uddg"):
        if qs.get(key) and qs[key][0].startswith("http"):
            return _canonical_social_url(unquote(qs[key][0]))

    host = _RE_SEARCH_REDIRECT.sub("", (parsed.netloc or "").lower())
    path = re.sub(r"/+", "/", parsed.path or "/")
    path = unquote(path).strip()

    if "tiktok.com" in host:
        m = _RE_TIKTOK.search(path)
        if m:
            suffix = f"/video/{m.group(2)}" if m.group(2) else ""
            return f"https://www.tiktok.com/@{m.group(1)}{suffix}"
        m = _RE_TIKTOK_TAG.search(path)
        if m:
            return f"https://www.tiktok.com/tag/{m.group(1)}"

    if "instagram.com" in host:
        m = _RE_INSTAGRAM.search(path)
        if m:
            kind = "reel" if m.group(1) in {"reel", "reels"} else m.group(1)
            return f"https://www.instagram.com/{kind}/{m.group(2)}/"
        m = _RE_INSTAGRAM_USER.match(path)
        if m:
            return f"https://www.instagram.com/{m.group(1)}/"

    if "snapchat.com" in host:
        m = _RE_SNAPCHAT.search(path)
        if m:
            return f"https://www.snapchat.com/add/{m.group(1)}"

    if "youtube.com" in host or "youtu.be" in host:
        if "youtu.be" in host:
            vid = path.strip("/").split("/")[0]
            return f"https://www.youtube.com/watch?v={vid}" if vid else raw
        if path.startswith("/watch"):
            vid = qs.get("v", [""])[0]
            return f"https://www.youtube.com/watch?v={vid}" if vid else raw
        m = _RE_YOUTUBE_CHANNEL.match(path)
        if m:
            return f"https://www.youtube.com/{m.group(1)}"

    if "twitter.com" in host or host == "x.com":
        m = _RE_TWITTER.match(path)
        if m:
            suffix = f"/status/{m.group(2)}" if m.group(2) else ""
            return f"https://twitter.com/{m.group(1)}{suffix}"

    cleaned = parsed._replace(fragment="")
    if cleaned.scheme not in ("http", "https"):
        cleaned = cleaned._replace(scheme="https")
    return urlunparse(cleaned)


def _output_path(output_dir: Path, name: str, fmt: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{name}.{fmt}"


def _clean_text(text: str, max_len: int = 50000) -> str:
    return text[:max_len] if text else ""


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _validate_url(url: str) -> tuple[bool, str]:
    """Validate and normalize a URL. Returns (is_valid, normalized_or_error)."""
    if not url or not url.strip():
        return False, "Empty URL"
    url = url.strip()
    if not _RE_URL_SCHEME.match(url):
        url = "https://" + url.lstrip("/")
    try:
        parsed = _urlparse(url)
    except Exception:
        return False, f"Invalid URL: {url}"
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme: {parsed.scheme}"
    if not parsed.netloc:
        return False, f"No hostname in URL: {url}"
    host = parsed.netloc.split(":")[0].lower()
    if _RE_PRIVATE_HOST.match(host):
        return False, f"Blocked private address: {host}"
    if _RE_PRIVATE_PREFIX.match(host):
        return False, f"Blocked private address: {host}"
    return True, url


def _validate_urls(urls: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Validate a list of URLs. Returns (valid_urls, [(url, error), ...])."""
    valid, errors = [], []
    seen = set()
    for url in urls:
        ok, result = _validate_url(url)
        if ok:
            key = result.lower().rstrip("/")
            if key not in seen:
                seen.add(key)
                valid.append(result)
        else:
            errors.append((url, result))
    return valid, errors


# ---------------------------------------------------------------------------
# Result cache (LRU & SQLite Persistent)
# ---------------------------------------------------------------------------
import sqlite3
import threading

_RESULT_CACHE: dict[str, dict] = {}
_CACHE_MAX = 500
_db_lock = threading.Lock()
_DB_PATH = Path(__file__).parent / "scrapling_cache.db"


def _db_init():
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=10)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scrapes (
                    url TEXT,
                    mode TEXT,
                    timestamp REAL,
                    data TEXT,
                    response_time REAL,
                    status INTEGER,
                    error TEXT,
                    platform TEXT,
                    username TEXT,
                    PRIMARY KEY (url, mode)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    timestamp REAL,
                    query TEXT,
                    results_count INTEGER,
                    details TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audience_runs (
                    id TEXT PRIMARY KEY,
                    target_url TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    username TEXT NOT NULL,
                    expected_total INTEGER DEFAULT 0,
                    collected INTEGER DEFAULT 0,
                    pages INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    phase TEXT DEFAULT '',
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    error TEXT DEFAULT '',
                    details TEXT DEFAULT '{}'
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audience_followers (
                    platform TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    sec_uid TEXT DEFAULT '',
                    username TEXT DEFAULT '',
                    full_name TEXT DEFAULT '',
                    profile_url TEXT DEFAULT '',
                    avatar_url TEXT DEFAULT '',
                    is_verified INTEGER DEFAULT 0,
                    is_private INTEGER DEFAULT 0,
                    followers INTEGER DEFAULT 0,
                    following INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    videos INTEGER DEFAULT 0,
                    run_id TEXT NOT NULL,
                    collected_at REAL NOT NULL,
                    PRIMARY KEY (platform, target_username, user_id)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_audience_followers_run "
                "ON audience_followers(run_id, collected_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_audience_followers_target "
                "ON audience_followers(platform, target_username, username)"
            )
            conn.commit()
        finally:
            conn.close()


# Initialize database at startup
try:
    _db_init()
except Exception as e:
    _logger.warning(f"Failed to initialize SQLite database: {e}")


def _db_add_history(run_type: str, query: str, results_count: int, details: dict = None):
    try:
        with _db_lock:
            conn = sqlite3.connect(str(_DB_PATH), timeout=5)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO history (type, timestamp, query, results_count, details) VALUES (?, ?, ?, ?, ?)",
                    (
                        run_type,
                        time.time(),
                        query,
                        results_count,
                        json.dumps(details or {}, ensure_ascii=False)
                    )
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        _logger.warning(f"Add history to SQLite failed: {e}")


def _cache_key(url: str, mode: str) -> str:
    return hashlib.md5(f"{mode}:{url.lower().rstrip('/')}".encode()).hexdigest()


def _cache_get(url: str, mode: str) -> dict | None:
    key = _cache_key(url, mode)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]
    try:
        with _db_lock:
            conn = sqlite3.connect(str(_DB_PATH), timeout=5)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT data FROM scrapes WHERE url = ? AND mode = ?",
                    (url.lower().rstrip("/"), mode)
                )
                row = cursor.fetchone()
                if row:
                    data = json.loads(row[0])
                    _RESULT_CACHE[key] = data
                    return data
            finally:
                conn.close()
    except Exception as e:
        _logger.warning(f"Cache get from SQLite failed: {e}")
    return None


def _cache_set(url: str, mode: str, data: dict):
    key = _cache_key(url, mode)
    if len(_RESULT_CACHE) >= _CACHE_MAX:
        oldest = next(iter(_RESULT_CACHE))
        del _RESULT_CACHE[oldest]
    _RESULT_CACHE[key] = data
    try:
        with _db_lock:
            conn = sqlite3.connect(str(_DB_PATH), timeout=5)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO scrapes
                    (url, mode, timestamp, data, response_time, status, error, platform, username)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        url.lower().rstrip("/"),
                        mode,
                        time.time(),
                        json.dumps(data, ensure_ascii=False),
                        data.get("response_time", 0.0),
                        data.get("status", 0),
                        data.get("error", ""),
                        data.get("platform", ""),
                        data.get("username", "")
                    )
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        _logger.warning(f"Cache set to SQLite failed: {e}")


# ---------------------------------------------------------------------------
# Authorized audience extraction
# ---------------------------------------------------------------------------

_AUDIENCE_JOBS: dict[str, dict] = {}
_AUDIENCE_JOBS_LOCK = threading.Lock()
_TIKTOK_AUTH_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_guard", "uid_tt"}


def _audience_expected_total(username: str, platform: str = "tiktok") -> int:
    """Return the strongest cached follower count for the target profile."""
    target = f"https://www.{platform}.com/@{username}".lower().rstrip("/")
    best = 0
    try:
        with _db_lock:
            conn = sqlite3.connect(str(_DB_PATH), timeout=5)
            try:
                rows = conn.execute(
                    "SELECT data FROM scrapes WHERE url = ?",
                    (target,),
                ).fetchall()
            finally:
                conn.close()
        for (raw,) in rows:
            try:
                data = json.loads(raw or "{}")
            except (TypeError, ValueError):
                continue
            best = max(best, _to_int_count(data.get("followers")))
    except Exception as exc:
        _logger.debug("Audience expected-total lookup failed: %s", exc)
    return best


def _normalize_tiktok_follower(item: dict) -> dict:
    """Normalize one TikTok ``api/user/list`` item without retaining contact PII."""
    item = item if isinstance(item, dict) else {}
    user = item.get("user") if isinstance(item.get("user"), dict) else item
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    if not stats and isinstance(user.get("stats"), dict):
        stats = user.get("stats")
    stats_v2 = item.get("statsV2") if isinstance(item.get("statsV2"), dict) else {}
    if not stats_v2 and isinstance(user.get("statsV2"), dict):
        stats_v2 = user.get("statsV2")
    user_id = str(user.get("id") or user.get("uid") or user.get("userId") or "").strip()
    sec_uid = str(user.get("secUid") or user.get("sec_uid") or "").strip()
    username = str(user.get("uniqueId") or user.get("unique_id") or "").strip().lstrip("@")
    full_name = str(user.get("nickname") or user.get("displayName") or "").strip()
    avatar = str(
        user.get("avatarLarger")
        or user.get("avatarMedium")
        or user.get("avatarThumb")
        or ""
    ).strip()

    def count(name: str, fallback: str = "") -> int:
        return _to_int_count(stats_v2.get(name) or stats.get(name) or stats.get(fallback))

    return {
        "user_id": user_id or sec_uid or username,
        "sec_uid": sec_uid,
        "username": username,
        "full_name": full_name,
        "profile_url": f"https://www.tiktok.com/@{username}" if username else "",
        "avatar_url": avatar,
        "is_verified": bool(user.get("verified", False)),
        "is_private": bool(user.get("privateAccount", False)),
        "followers": count("followerCount"),
        "following": count("followingCount"),
        "likes": count("heartCount", "heart"),
        "videos": count("videoCount"),
    }


def _audience_create_run(run_id: str, target_url: str, platform: str, username: str,
                         expected_total: int) -> None:
    now = time.time()
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=10)
        try:
            conn.execute(
                """
                INSERT INTO audience_runs
                (id, target_url, platform, username, expected_total, collected,
                 pages, status, phase, started_at, updated_at, details)
                VALUES (?, ?, ?, ?, ?, 0, 0, 'queued', 'Preparing browser', ?, ?, '{}')
                """,
                (run_id, target_url, platform, username, expected_total, now, now),
            )
            conn.commit()
        finally:
            conn.close()


def _audience_update_run(run_id: str, **changes) -> None:
    allowed = {
        "expected_total", "collected", "pages", "status", "phase",
        "updated_at", "completed_at", "error", "details",
    }
    values = {key: value for key, value in changes.items() if key in allowed}
    values.setdefault("updated_at", time.time())
    if isinstance(values.get("details"), dict):
        values["details"] = json.dumps(values["details"], ensure_ascii=False)
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=10)
        try:
            conn.execute(
                f"UPDATE audience_runs SET {assignments} WHERE id = ?",
                (*values.values(), run_id),
            )
            conn.commit()
        finally:
            conn.close()


def _audience_store_followers(run_id: str, platform: str, target_username: str,
                              rows: list[dict]) -> tuple[int, int]:
    """Upsert public follower identities; returns (new rows, target total stored)."""
    now = time.time()
    inserted = 0
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=15)
        try:
            for raw in rows:
                row = _normalize_tiktok_follower(raw) if platform == "tiktok" else raw
                if not row.get("user_id"):
                    continue
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT INTO audience_followers
                    (platform, target_username, user_id, sec_uid, username,
                     full_name, profile_url, avatar_url, is_verified, is_private,
                     followers, following, likes, videos, run_id, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, target_username, user_id) DO UPDATE SET
                        sec_uid=excluded.sec_uid,
                        username=excluded.username,
                        full_name=excluded.full_name,
                        profile_url=excluded.profile_url,
                        avatar_url=excluded.avatar_url,
                        is_verified=excluded.is_verified,
                        is_private=excluded.is_private,
                        followers=excluded.followers,
                        following=excluded.following,
                        likes=excluded.likes,
                        videos=excluded.videos,
                        run_id=excluded.run_id,
                        collected_at=excluded.collected_at
                    """,
                    (
                        platform, target_username, row.get("user_id", ""),
                        row.get("sec_uid", ""), row.get("username", ""),
                        row.get("full_name", ""), row.get("profile_url", ""),
                        row.get("avatar_url", ""), int(bool(row.get("is_verified"))),
                        int(bool(row.get("is_private"))), int(row.get("followers") or 0),
                        int(row.get("following") or 0), int(row.get("likes") or 0),
                        int(row.get("videos") or 0), run_id, now,
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
            total = conn.execute(
                "SELECT COUNT(*) FROM audience_followers "
                "WHERE platform = ? AND target_username = ?",
                (platform, target_username),
            ).fetchone()[0]
            conn.commit()
        finally:
            conn.close()
    return inserted, total


def _audience_run_snapshot(run_id: str, *, limit: int = 100, offset: int = 0,
                           search: str = "") -> dict | None:
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            run = conn.execute(
                "SELECT * FROM audience_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not run:
                return None
            params: list[Any] = [run["platform"], run["username"]]
            where = "platform = ? AND target_username = ?"
            if search:
                where += " AND (username LIKE ? OR full_name LIKE ?)"
                pattern = f"%{search[:100]}%"
                params.extend([pattern, pattern])
            rows = conn.execute(
                f"""
                SELECT user_id, sec_uid, username, full_name, profile_url,
                       avatar_url, is_verified, is_private, followers, following,
                       likes, videos, collected_at
                FROM audience_followers
                WHERE {where}
                ORDER BY collected_at DESC, followers DESC
                LIMIT ? OFFSET ?
                """,
                (*params, max(1, min(limit, 500)), max(0, offset)),
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM audience_followers WHERE {where}",
                params,
            ).fetchone()[0]
        finally:
            conn.close()
    result = dict(run)
    try:
        result["details"] = json.loads(result.get("details") or "{}")
    except (TypeError, ValueError):
        result["details"] = {}
    expected = int(result.get("expected_total") or 0)
    collected = int(result.get("collected") or 0)
    result["progress"] = round((collected / expected) * 100, 4) if expected else 0
    result["followers"] = [dict(row) for row in rows]
    result["result_total"] = total
    return result


def _audience_all_rows(run_id: str) -> tuple[dict | None, list[dict]]:
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            run = conn.execute(
                "SELECT * FROM audience_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not run:
                return None, []
            rows = conn.execute(
                """
                SELECT profile_url, full_name, username, is_verified, is_private,
                       followers, following, likes, videos, user_id, sec_uid,
                       collected_at
                FROM audience_followers
                WHERE platform = ? AND target_username = ?
                ORDER BY collected_at, username
                """,
                (run["platform"], run["username"]),
            ).fetchall()
        finally:
            conn.close()
    return dict(run), [dict(row) for row in rows]


def _audience_latest_run(target_username: str, platform: str = "tiktok") -> dict | None:
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=5)
        try:
            row = conn.execute(
                "SELECT id FROM audience_runs WHERE platform = ? AND username = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (platform, target_username),
            ).fetchone()
        finally:
            conn.close()
    return _audience_run_snapshot(row[0]) if row else None


def _audience_has_active_run(platform: str, username: str) -> str:
    with _db_lock:
        conn = sqlite3.connect(str(_DB_PATH), timeout=5)
        try:
            row = conn.execute(
                "SELECT id FROM audience_runs WHERE platform = ? AND username = ? "
                "AND status IN ('queued','starting','awaiting_login','awaiting_challenge','collecting') "
                "ORDER BY started_at DESC LIMIT 1",
                (platform, username),
            ).fetchone()
        finally:
            conn.close()
    return row[0] if row else ""


def _audience_job_cancelled(run_id: str) -> bool:
    with _AUDIENCE_JOBS_LOCK:
        job = _AUDIENCE_JOBS.get(run_id)
        return bool(job and job["cancel"].is_set())


async def _collect_tiktok_followers(run_id: str, username: str, expected_total: int,
                                    max_followers: int = 0, login_timeout: int = 900) -> None:
    """Run an authorized, ephemeral Scrapling browser session and consume follower-list XHR pages."""
    target_url = f"https://www.tiktok.com/@{username}"
    state = {
        "pages": 0,
        "collected": 0,
        "has_more": None,
        "target_seen": False,
        "last_page_at": 0.0,
        "stalled": False,
    }
    response_tasks: set[asyncio.Task] = set()

    def update(**changes):
        _audience_update_run(run_id, **changes)

    async def handle_response(response):
        url = str(getattr(response, "url", "") or "")
        if "/api/user/list/" not in url or "targetUserId=" not in url:
            return
        try:
            payload = await response.json()
        except Exception:
            return
        if not isinstance(payload, dict) or int(payload.get("statusCode", payload.get("status_code", -1))) != 0:
            return
        users = payload.get("userList") if isinstance(payload.get("userList"), list) else []
        _, total = _audience_store_followers(run_id, "tiktok", username, users)
        state["pages"] += 1
        state["collected"] = total
        state["target_seen"] = True
        state["has_more"] = bool(payload.get("hasMore", False))
        state["last_page_at"] = time.monotonic()
        update(
            status="collecting",
            phase=f"Captured page {state['pages']:,}",
            collected=total,
            pages=state["pages"],
            details={
                "has_more": state["has_more"],
                "public_page_size": len(users),
                "session_storage": "ephemeral",
            },
        )

    async def page_setup(page):
        def on_response(response):
            task = asyncio.create_task(handle_response(response))
            response_tasks.add(task)
            task.add_done_callback(response_tasks.discard)
        page.on("response", on_response)

    async def is_authenticated(page) -> bool:
        try:
            cookies = await page.context.cookies()
            return bool({cookie.get("name") for cookie in cookies} & _TIKTOK_AUTH_COOKIE_NAMES)
        except Exception:
            return False

    async def wait_for_auth(page) -> bool:
        update(
            status="awaiting_login",
            phase="Sign in in the opened TikTok window",
            details={
                "action": "Complete TikTok sign-in in the visible browser.",
                "credentials_saved": False,
            },
        )
        if "/login" not in page.url:
            await page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")
        deadline = time.monotonic() + login_timeout
        while time.monotonic() < deadline:
            if _audience_job_cancelled(run_id):
                return False
            if await is_authenticated(page):
                return True
            await page.wait_for_timeout(1000)
        return False

    async def wait_for_profile(page) -> bool:
        await page.goto(target_url, wait_until="domcontentloaded")
        deadline = time.monotonic() + 180
        challenge_announced = False
        while time.monotonic() < deadline:
            if _audience_job_cancelled(run_id):
                return False
            follower_count = page.locator('[data-e2e="followers-count"]')
            if await follower_count.count():
                try:
                    if await follower_count.first.is_visible():
                        return True
                except Exception:
                    pass
            try:
                text = (await page.locator("body").inner_text()).lower()
            except Exception:
                text = ""
            if any(signal in text for signal in ("drag the slider", "verify to continue", "security verification")):
                if not challenge_announced:
                    update(
                        status="awaiting_challenge",
                        phase="Complete TikTok's visible verification",
                        details={"action": "Solve the challenge in the opened browser window."},
                    )
                    challenge_announced = True
            await page.wait_for_timeout(1000)
        return False

    async def scroll_audience_modal(page) -> bool:
        return bool(await page.evaluate(
            """
            () => {
              const roots = [...document.querySelectorAll('[role="dialog"], [data-e2e*="follow"]')];
              const candidates = roots.flatMap(root => [root, ...root.querySelectorAll('*')]);
              const scrollable = candidates
                .filter(el => el.scrollHeight > el.clientHeight + 80)
                .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
              if (!scrollable) return false;
              scrollable.scrollTop = scrollable.scrollHeight;
              scrollable.dispatchEvent(new Event('scroll', { bubbles: true }));
              return true;
            }
            """
        ))

    async def page_action(page):
        update(status="starting", phase="Opening authorized Scrapling browser")
        await page.wait_for_timeout(5000)
        if not await is_authenticated(page):
            if not await wait_for_auth(page):
                if _audience_job_cancelled(run_id):
                    update(status="cancelled", phase="Cancelled", completed_at=time.time())
                else:
                    update(
                        status="auth_timeout",
                        phase="TikTok sign-in was not completed",
                        error="Authorized extraction requires a completed TikTok sign-in.",
                        completed_at=time.time(),
                    )
                return
        if not await wait_for_profile(page):
            if _audience_job_cancelled(run_id):
                update(status="cancelled", phase="Cancelled", completed_at=time.time())
            else:
                update(
                    status="blocked",
                    phase="Profile audience control was not accessible",
                    error="TikTok did not expose the follower control after sign-in/verification.",
                    completed_at=time.time(),
                )
            return

        follower_count = page.locator('[data-e2e="followers-count"]')
        update(status="starting", phase="Opening Roxa Shop follower list")
        await follower_count.first.click(timeout=15000)
        open_deadline = time.monotonic() + 25
        while time.monotonic() < open_deadline and not state["target_seen"]:
            if _audience_job_cancelled(run_id):
                update(status="cancelled", phase="Cancelled", completed_at=time.time())
                return
            await page.wait_for_timeout(500)
        if not state["target_seen"]:
            update(
                status="blocked",
                phase="Follower list response was not exposed",
                error="TikTok kept the follower list behind an access restriction.",
                completed_at=time.time(),
            )
            return

        update(status="collecting", phase="Consuming follower pages")
        quiet_cycles = 0
        previous_pages = state["pages"]
        while True:
            if _audience_job_cancelled(run_id):
                update(status="cancelled", phase="Cancelled", completed_at=time.time())
                return
            if max_followers and state["collected"] >= max_followers:
                break
            if expected_total and state["collected"] >= expected_total:
                break
            if state["has_more"] is False:
                break
            scrolled = await scroll_audience_modal(page)
            if not scrolled:
                await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(2750)
            if state["pages"] == previous_pages:
                quiet_cycles += 1
            else:
                quiet_cycles = 0
                previous_pages = state["pages"]
            if quiet_cycles >= 8:
                state["stalled"] = True
                break

        if response_tasks:
            await asyncio.gather(*list(response_tasks), return_exceptions=True)
        collected = state["collected"]
        reached_all = bool(expected_total and collected >= expected_total)
        limited_run = bool(max_followers and collected >= max_followers and not reached_all)
        if reached_all:
            status, phase, error = "complete", "All followers collected", ""
        elif limited_run:
            status, phase, error = "limit_reached", "Requested collection limit reached", ""
        else:
            status, phase = "partial", "TikTok ended the exposed follower stream"
            error = (
                f"Collected {collected:,} public follower records out of the "
                f"{expected_total:,} profile count. TikTok did not expose another page."
                if expected_total else
                "TikTok did not expose another follower page."
            )
        update(
            status=status,
            phase=phase,
            collected=collected,
            pages=state["pages"],
            error=error,
            completed_at=time.time(),
            details={
                "has_more": state["has_more"],
                "stalled": state["stalled"],
                "credentials_saved": False,
                "session_storage": "ephemeral",
            },
        )

    try:
        with tempfile.TemporaryDirectory(prefix="scrapling-audience-") as session_dir:
            collection_goal = max_followers or expected_total or 300
            expected_pages = max(1, (collection_goal + 29) // 30)
            # A full 211K audience can require thousands of paced modal loads.
            # Size the browser lifetime to the requested run, capped at 24 hours.
            collector_timeout_ms = int(min(
                24 * 60 * 60,
                max(login_timeout + 240, expected_pages * 4 + 600),
            ) * 1000)
            await DynamicFetcher.async_fetch(
                target_url,
                timeout=collector_timeout_ms,
                headless=False,
                real_chrome=True,
                user_data_dir=session_dir,
                page_setup=page_setup,
                page_action=page_action,
                capture_xhr=r"api/user/list",
                network_idle=False,
                wait=500,
            )
    except Exception as exc:
        snapshot = _audience_run_snapshot(run_id, limit=1)
        if snapshot and snapshot.get("status") not in {
            "complete", "partial", "limit_reached", "cancelled",
            "auth_timeout", "blocked", "session_closed",
        }:
            message = str(exc)[:500]
            browser_was_closed = (
                "target page, context or browser has been closed" in message.lower()
            )
            update(
                status="session_closed" if browser_was_closed else "failed",
                phase=(
                    "Secure TikTok window was closed"
                    if browser_was_closed else "Collector stopped"
                ),
                error=message,
                completed_at=time.time(),
                details={
                    "action": (
                        "Start a new run and keep the temporary TikTok window open "
                        "until follower collection begins."
                        if browser_was_closed else
                        "Review the collector error and start a new run."
                    ),
                    "credentials_saved": False,
                    "session_storage": "ephemeral",
                },
            )


def _start_audience_job(target_url: str, max_followers: int = 0) -> tuple[str, bool]:
    platform = _platform_of(target_url)
    username = _username_from_url(target_url).lstrip("@")
    active = _audience_has_active_run(platform, username)
    if active:
        return active, False
    run_id = uuid.uuid4().hex
    expected = _audience_expected_total(username, platform)
    _audience_create_run(run_id, target_url, platform, username, expected)
    cancel_event = threading.Event()

    def worker():
        try:
            asyncio.run(_collect_tiktok_followers(
                run_id,
                username,
                expected,
                max_followers=max_followers,
            ))
        finally:
            with _AUDIENCE_JOBS_LOCK:
                _AUDIENCE_JOBS.pop(run_id, None)

    thread = threading.Thread(
        target=worker,
        name=f"audience-{run_id[:8]}",
        daemon=True,
    )
    with _AUDIENCE_JOBS_LOCK:
        _AUDIENCE_JOBS[run_id] = {"thread": thread, "cancel": cancel_event}
    thread.start()
    return run_id, True


# ---------------------------------------------------------------------------
# Instagram parser - comprehensive profile extraction
# ---------------------------------------------------------------------------

def _parse_num(text: str) -> str:
    """Normalize Instagram-style numbers like '12K', '1.5M', '2,345' to clean strings."""
    if not text:
        return ""
    text = text.strip().replace(",", "").replace("\u00a0", " ")
    return text


def _extract_shared_data(raw: str) -> dict:
    """Extract the __sharedData JSON blob from Instagram page source."""
    m = _RE_SHARED_DATA.search(raw)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return {}


def _extract_additional_data(raw: str) -> dict:
    """Extract __additionalData json."""
    for r in _RE_ADDITIONAL_DATA.findall(raw):
        try:
            return json.loads(r)
        except json.JSONDecodeError:
            pass
    return {}


def parse_instagram(response, url: str) -> dict:
    """Extract ALL available Instagram profile data from a page response."""
    username = _username_from_url(url)
    raw = _safe(response.body)

    result = {
        "url": url,
        "username": username,
        "status": getattr(response, "status", 200),
        "full_name": "",
        "bio": "",
        "followers": "",
        "following": "",
        "posts_count": "",
        "videos_count": "",
        "is_verified": False,
        "is_business": False,
        "is_private": False,
        "business_category": "",
        "external_url": "",
        "profile_pic": "",
        "profile_pic_hd": "",
        "hashtags": [],
        "mentions": [],
        "avg_likes": "",
        "recent_posts": [],
        "title": "",
        "description": "",
        "error": "",
        "logged_in": False,
    }

    # --- Detect login wall ---
    has_og_desc = bool(response.css('meta[property="og:description"]::attr(content)').get())
    is_login_page = bool(_RE_LOGIN_WALL.search(raw)) or not has_og_desc
    if is_login_page:
        result["error"] = "login_required"
        return result

    result["logged_in"] = not is_login_page

    # --- JSON-LD (structured data) ---
    for block in _RE_JSON_LD.findall(raw):
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                result["full_name"] = data.get("name") or result["full_name"]
                result["description"] = data.get("description") or result["description"]
                if isinstance(data.get("image"), dict):
                    result["profile_pic"] = data["image"].get("contentUrl", "") or result["profile_pic"]
                if data.get("url"):
                    pass  # same as username
        except json.JSONDecodeError:
            pass

    # --- Open Graph meta tags ---
    og_title = _safe(response.css('meta[property="og:title"]::attr(content)').get())
    og_desc = _safe(response.css('meta[property="og:description"]::attr(content)').get())
    result["title"] = og_title or result["full_name"] or username
    result["description"] = og_desc or result.get("description", "")

    if not result["profile_pic"]:
        result["profile_pic"] = _safe(
            response.css('meta[property="og:image"]::attr(content)').get()
        )

    # --- Profile pic HD ---
    result["profile_pic_hd"] = _safe(
        response.css('meta[property="og:image:secure_url"]::attr(content)').get()
    ) or result["profile_pic"]

    # --- Parse description for stats ---
    if og_desc:
        for raw_val, label in _RE_OG_DESC_STATS.findall(_safe(og_desc)):
            label_lower = label.lower()
            if "post" in label_lower:
                result["posts_count"] = _parse_num(raw_val)
            elif "follower" in label_lower:
                result["followers"] = _parse_num(raw_val)
            elif "following" in label_lower:
                result["following"] = _parse_num(raw_val)

    # --- External URL in bio ---
    result["external_url"] = _safe(
        response.css('a[href*="?igsh"]::attr(href)').get()
        or response.css('[data-testid="UserExternalLink"]::attr(href)').get()
        or ""
    )
    if not result["external_url"]:
        m = _RE_EXTERNAL_URL.search(raw)
        if m:
            result["external_url"] = m.group(1).replace("\\/", "/")

    # --- Bio and category ---
    if og_desc:
        lines = og_desc.strip().split("\n")
        non_empty = [line.strip() for line in lines if line.strip()]
        if non_empty:
            bio = non_empty[0]
            if "Followers" in bio or "Following" in bio or "Posts" in bio:
                bio = "\n".join(
                    line for line in non_empty
                    if "Followers" not in line and "Following" not in line and "Posts" not in line
                )
            result["bio"] = bio.strip()

    if not result["bio"]:
        result["bio"] = result.get("description", "")

    # --- Hashtags & mentions from bio ---
    bio_text = result["bio"]
    result["hashtags"] = list(set(re.findall(r"#(\w+)", bio_text)))
    result["mentions"] = list(set(re.findall(r"@(\w+)", bio_text)))

    # --- Business category ---
    m = re.search(r'"category_name"\s*:\s*"([^"]+)"', raw)
    if m:
        result["business_category"] = m.group(1)
        result["is_business"] = True

    m2 = re.search(r'"is_business_account"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m2:
        result["is_business"] = m2.group(1).lower() == "true"

    # --- Verified ---
    m3 = re.search(r'"is_verified"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m3:
        result["is_verified"] = m3.group(1).lower() == "true"
    m_private = re.search(r'"is_private"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m_private:
        result["is_private"] = m_private.group(1).lower() == "true"

    # --- Full name from meta ---
    m4 = re.search(r'"full_name"\s*:\s*"([^"]+)"', raw)
    if m4:
        result["full_name"] = m4.group(1).replace("\\/", "/")

    # --- Videos count ---
    m5 = re.search(r'"video_count"\s*:\s*(\d+)', raw)
    if m5:
        result["videos_count"] = m5.group(1)

    # --- Posts count from JSON fallback ---
    if not result["posts_count"]:
        m6 = re.search(r'"media_count"\s*:\s*(\d+)', raw)
        if m6:
            result["posts_count"] = m6.group(1)

    # --- Followers/following from JSON fallback ---
    if not result["followers"]:
        m7 = re.search(r'"follower_count"\s*:\s*(\d+)', raw)
        if m7:
            result["followers"] = _parse_num(m7.group(1))
    if not result["following"]:
        m8 = re.search(r'"following_count"\s*:\s*(\d+)', raw)
        if m8:
            result["following"] = _parse_num(m8.group(1))

    # --- Recent posts (from embedded JSON in page source) ---
    reels_m = re.findall(
        r'"node"\s*:\s*\{[^}]*"display_url"\s*:\s*"([^"]+)"[^}]*"edge_liked_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        raw
    )
    if not reels_m:
        reels_m = re.findall(
            r'"likes"\s*:\s*\{\s*"count"\s*:\s*(\d+)\s*\}',
            raw
        )

    posts_preview = []
    if reels_m:
        for match in reels_m[:12]:
            if isinstance(match, tuple) and len(match) == 2:
                img_url, likes = match
                posts_preview.append({"likes": likes, "display_url": img_url[:100]})
            elif isinstance(match, str):
                posts_preview.append({"likes": match})

    if posts_preview:
        likes_vals = [int(p["likes"]) for p in posts_preview if p["likes"].isdigit()]
        if likes_vals:
            result["avg_likes"] = f"{sum(likes_vals) // len(likes_vals):,}"

    result["recent_posts"] = posts_preview[:12]

    return result


# ---------------------------------------------------------------------------
# TikTok parser
# ---------------------------------------------------------------------------

def parse_tiktok(response, url: str) -> dict:
    """Extract TikTok profile data from a page response."""
    username = _username_from_url(url)
    raw = _safe(response.body)
    result = {
        "url": url,
        "username": username,
        "platform": "tiktok",
        "status": getattr(response, "status", 200),
        "full_name": "",
        "bio": "",
        "followers": "",
        "following": "",
        "likes": "",
        "videos_count": "",
        "is_verified": False,
        "is_business": False,
        "is_private": False,
        "profile_pic": "",
        "hashtags": [],
        "error": "",
        "logged_in": False,
    }

    # --- JSON-LD ---
    json_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', raw, re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                result["full_name"] = data.get("name") or result["full_name"]
                result["description"] = data.get("description") or ""
        except json.JSONDecodeError:
            pass

    # --- Meta tags ---
    og_title = _safe(response.css('meta[property="og:title"]::attr(content)').get())
    og_desc = _safe(response.css('meta[property="og:description"]::attr(content)').get())
    result["full_name"] = og_title or result["full_name"] or username
    result["profile_pic"] = _safe(response.css('meta[property="og:image"]::attr(content)').get())

    # --- Stats from og:description ---
    if og_desc:
        m_followers = re.search(r"([\d.,]+[KMBkmb]?)\s*Followers", og_desc)
        if m_followers:
            result["followers"] = m_followers.group(1)
        m_following = re.search(r"([\d.,]+[KMBkmb]?)\s*Following", og_desc)
        if m_following:
            result["following"] = m_following.group(1)
        m_likes = re.search(r"([\d.,]+[KMBkmb]?)\s*Likes", og_desc)
        if m_likes:
            result["likes"] = m_likes.group(1)
        # Bio is the full description text
        result["bio"] = og_desc

    # --- JSON embedded data ---
    m_verified = re.search(r'"verified"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m_verified:
        result["is_verified"] = m_verified.group(1).lower() == "true"
    m_private = re.search(r'"privateAccount"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m_private:
        result["is_private"] = m_private.group(1).lower() == "true"

    m_follower = re.search(r'"followerCount"\s*:\s*(\d+)', raw)
    if m_follower:
        result["followers"] = m_follower.group(1)
    m_follow = re.search(r'"followingCount"\s*:\s*(\d+)', raw)
    if m_follow:
        result["following"] = m_follow.group(1)
    m_like = re.search(r'"heartCount"\s*:\s*(\d+)', raw)
    if m_like:
        result["likes"] = m_like.group(1)
    m_video = re.search(r'"videoCount"\s*:\s*(\d+)', raw)
    if m_video:
        result["videos_count"] = m_video.group(1)
    m_nick = re.search(r'"nickname"\s*:\s*"([^"]+)"', raw)
    if m_nick:
        result["full_name"] = m_nick.group(1)

    # --- Hashtags from bio ---
    bio_text = result.get("bio", "")
    result["hashtags"] = list(set(re.findall(r"#(\w+)", bio_text)))

    # --- Detect login/blocked ---
    if re.search(r'/(login|signup)/', raw[:3000]) or "This account is private" in raw[:5000]:
        result["error"] = "login_required"

    return result


# ---------------------------------------------------------------------------
# Generic page parser
# ---------------------------------------------------------------------------

def parse_snapchat(response, url: str) -> dict:
    """Extract Snapchat profile data from meta description."""
    username = _username_from_url(url)
    result = {
        "url": url,
        "username": f"@{username}",
        "platform": "snapchat",
        "status": getattr(response, "status", 200),
        "full_name": "",
        "subscribers": "",
        "bio": "",
        "location": "",
        "last_updated": "",
        "is_verified": False,
        "profile_pic": "",
        "error": "",
    }
    desc = _safe(
        response.css('meta[name="description"]::attr(content)').get()
        or response.css('meta[property="og:description"]::attr(content)').get()
        or ""
    )
    title = _safe(
        response.css("title::text").get()
        or response.css('meta[property="og:title"]::attr(content)').get()
        or ""
    )
    og_image = _safe(response.css('meta[property="og:image"]::attr(content)').get() or "")

    # Full name from title: "Name (@user) | ..."
    m = re.match(r'^(.+?)\s*\(@', title)
    if m:
        result["full_name"] = m.group(1).strip()

    if og_image:
        result["profile_pic"] = og_image

    # Parse description fields separated by |
    if desc:
        parts = [p.strip() for p in desc.split("|")]
        for part in parts:
            # Subscribers count
            sub_m = re.search(r'عدد\s*المشتركين\s*:\s*([\d.]+[kKmMbB]?)', part)
            if sub_m:
                result["subscribers"] = sub_m.group(1).strip().lower()
                continue
            # Last updated
            upd_m = re.search(r'آخر\s*تحديث\s*:\s*(.+)', part)
            if upd_m:
                result["last_updated"] = upd_m.group(1).strip()
                continue
            # Location - any Arabic/non-URL text that isn't one of the known fields
            if not re.search(r'عدد\s*المشتركين|آخر\s*تحديث|@\w+|سناب\s*شات', part) and not result["location"]:
                # Check if it looks like a location (not a URL, not empty)
                if part and not part.startswith("http"):
                    result["location"] = part

        # If no location parsed from parts, check for remaining parts
        if not result["location"]:
            for part in parts:
                if not re.search(r'عدد\s*المشتركين|آخر\s*تحديث|@\w+|سناب\s*شات', part) and not part.startswith("http") and part.strip():
                    result["location"] = part.strip()
                    break

        # Bio = first part of description before the first |
        if parts:
            bio_part = parts[0].strip()
            result["bio"] = bio_part

    # Verification check (Snapchat verified badge in meta)
    if "verified" in _safe(response.css('[class*="verified"]::text').get() or "").lower():
        result["is_verified"] = True

    return result


# ---------------------------------------------------------------------------
# YouTube parser
# ---------------------------------------------------------------------------

def parse_youtube(response, url: str) -> dict:
    """Extract YouTube channel/profile data from a page response."""
    username = _username_from_url(url)
    raw = _safe(response.body)
    result = {
        "url": url,
        "username": username.lstrip("@"),
        "platform": "youtube",
        "status": getattr(response, "status", 200),
        "channel_name": "",
        "subscribers": "",
        "description": "",
        "profile_pic": "",
        "videos_count": "",
        "is_verified": False,
        "country": "",
        "joined": "",
        "total_views": "",
        "error": "",
    }

    # --- Meta tags ---
    og_title = _safe(response.css('meta[property="og:title"]::attr(content)').get())
    og_desc = _safe(response.css('meta[property="og:description"]::attr(content)').get())
    og_image = _safe(response.css('meta[property="og:image"]::attr(content)').get())
    result["channel_name"] = og_title or username
    result["description"] = og_desc or ""
    result["profile_pic"] = og_image or ""

    # --- JSON-LD ---
    json_blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', raw, re.DOTALL
    )
    for block in json_blocks:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                result["channel_name"] = data.get("name") or result["channel_name"]
                result["description"] = data.get("description") or result["description"]
                if data.get("image"):
                    result["profile_pic"] = data["image"] if isinstance(data["image"], str) else result["profile_pic"]
                interaction = data.get("interactionStatistic", [])
                if isinstance(interaction, list):
                    for stat in interaction:
                        stat_type = stat.get("interactionType", "")
                        count = stat.get("userInteractionCount", "")
                        if "Subscribe" in str(stat_type):
                            result["subscribers"] = str(count)
                elif isinstance(interaction, dict):
                    result["subscribers"] = str(interaction.get("userInteractionCount", ""))
        except (json.JSONDecodeError, TypeError):
            pass

    # --- Stats from embedded JSON ---
    m_subs = re.search(r'"subscriberCountText"[^}]*"simpleText"\s*:\s*"([^"]+)"', raw)
    if not m_subs:
        m_subs = re.search(r'"subscriberCountText"[^}]*"content"\s*:\s*"([^"]+)"', raw)
    if m_subs:
        result["subscribers"] = m_subs.group(1).strip()

    m_videos = re.search(r'"videosCountText"[^}]*"simpleText"\s*:\s*"([^"]+)"', raw)
    if m_videos:
        result["videos_count"] = m_videos.group(1).strip()

    m_country = re.search(r'"country"\s*:\s*"([^"]+)"', raw)
    if m_country:
        result["country"] = m_country.group(1)

    m_joined = re.search(r'"joinedDateText"[^}]*"content"\s*:\s*"([^"]+)"', raw)
    if m_joined:
        result["joined"] = m_joined.group(1).strip()

    m_views = re.search(r'"viewCountText"[^}]*"simpleText"\s*:\s*"([^"]+)"', raw)
    if m_views:
        result["total_views"] = m_views.group(1).strip()

    m_verified = re.search(r'"isVerified"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m_verified:
        result["is_verified"] = m_verified.group(1).lower() == "true"

    # Detect login/blocked
    if "This channel is private" in raw[:5000] or "Sign in" in og_title:
        result["error"] = "login_required"

    return result


# ---------------------------------------------------------------------------
# Twitter/X parser
# ---------------------------------------------------------------------------

def parse_twitter(response, url: str) -> dict:
    """Extract Twitter/X profile data from a page response."""
    username = _username_from_url(url)
    raw = _safe(response.body)
    result = {
        "url": url,
        "username": username.lstrip("@"),
        "platform": "twitter",
        "status": getattr(response, "status", 200),
        "full_name": "",
        "bio": "",
        "followers": "",
        "following": "",
        "posts_count": "",
        "likes_count": "",
        "is_verified": False,
        "profile_pic": "",
        "location": "",
        "joined": "",
        "external_url": "",
        "hashtags": [],
        "error": "",
    }

    # --- Meta tags ---
    og_title = _safe(response.css('meta[property="og:title"]::attr(content)').get())
    og_desc = _safe(response.css('meta[property="og:description"]::attr(content)').get())
    og_image = _safe(response.css('meta[property="og:image"]::attr(content)').get())
    title = _safe(response.css("title::text").get())
    desc = _safe(response.css('meta[name="description"]::attr(content)').get() or "")

    # Full name from title: "Name (@handle) / X"
    m_name = re.match(r'^(.+?)\s*\(@', og_title or title or "")
    if m_name:
        result["full_name"] = m_name.group(1).strip()
    result["profile_pic"] = og_image or ""
    result["bio"] = og_desc or desc or ""

    # --- Embedded JSON data ---
    m_followers = re.search(r'"followers_count"\s*:\s*(\d+)', raw)
    if m_followers:
        result["followers"] = m_followers.group(1)
    m_following = re.search(r'"friends_count"\s*:\s*(\d+)', raw)
    if m_following:
        result["following"] = m_following.group(1)
    m_posts = re.search(r'"statuses_count"\s*:\s*(\d+)', raw)
    if m_posts:
        result["posts_count"] = m_posts.group(1)
    m_likes = re.search(r'"favourites_count"\s*:\s*(\d+)', raw)
    if m_likes:
        result["likes_count"] = m_likes.group(1)
    m_loc = re.search(r'"location"\s*:\s*"([^"]*)"', raw)
    if m_loc:
        result["location"] = m_loc.group(1)
    m_url = re.search(r'"url"\s*:\s*"([^"]+)"', raw)
    if m_url:
        result["external_url"] = m_url.group(1).replace("\\/", "/")
    m_verified = re.search(r'"verified"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m_verified:
        result["is_verified"] = m_verified.group(1).lower() == "true"
    m_created = re.search(r'"created_at"\s*:\s*"([^"]+)"', raw)
    if m_created:
        result["joined"] = m_created.group(1)
    m_fullname = re.search(r'"name"\s*:\s*"([^"]+)"', raw)
    if m_fullname and not result["full_name"]:
        result["full_name"] = m_fullname.group(1)
    m_desc = re.search(r'"description"\s*:\s*"([^"]+)"', raw)
    if m_desc and not result["bio"]:
        result["bio"] = m_desc.group(1).replace("\\n", "\n")

    # Hashtags from bio
    bio_text = result.get("bio", "")
    result["hashtags"] = list(set(re.findall(r"#(\w+)", bio_text)))

    # Detect login wall
    if "Log in to X" in raw[:3000] or "login" in og_title.lower():
        result["error"] = "login_required"

    return result


def parse_generic(response: Selector, url: str) -> dict:
    return {
        "url": url,
        "username": _username_from_url(url),
        "platform": _platform_of(url),
        "status": response.status if hasattr(response, "status") else 200,
        "title": _safe(
            response.css("title::text").get()
            or response.css('meta[property="og:title"]::attr(content)').get()
            or ""
        ),
        "description": _safe(
            response.css('meta[name="description"]::attr(content)').get()
            or response.css('meta[property="og:description"]::attr(content)').get()
            or ""
        ),
        "profile_pic": _safe(
            response.css('meta[property="og:image"]::attr(content)').get()
            or response.css('meta[name="twitter:image"]::attr(content)').get()
            or ""
        ),
        "text_content": _safe(response.get_all_text(strip=True))[:50000],
        "links": [
            href for href in (response.css("a::attr(href)").getall() or [])
            if href.startswith("http")
        ],
        "headers": dict(response.headers) if hasattr(response, "headers") else {},
    }


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_content(response: Selector, css_selector: str = "") -> str:
    if not css_selector:
        html = _safe(response.body)
        return html
    elements = response.css(css_selector)
    if not elements:
        return ""
    return "\n\n".join(_safe(e.get()) for e in elements if e.get())


def extract_text(response: Selector, css_selector: str = "") -> str:
    if not css_selector:
        return _safe(response.get_all_text(strip=True))[:50000]
    elements = response.css(css_selector)
    if not elements:
        return ""
    return "\n\n".join(
        _safe(e.get_all_text(strip=True)) for e in elements if e.get_all_text(strip=True)
    )


def extract_markdown(response: Selector, css_selector: str = "") -> str:
    try:
        from markdownify import markdownify as md
        html = extract_content(response, css_selector)
        return md(html, heading_style="ATX", strip=["script", "style"])
    except ImportError:
        return extract_text(response, css_selector)


def extract_ai_targeted(response: Selector) -> str:
    """Sanitize content for AI consumption (strip hidden elements, prompt injection)."""
    raw = _safe(response.body)
    stripped = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', raw, flags=re.DOTALL)
    stripped = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'<noscript\b[^<]*(?:(?!<\/noscript>)<[^<]*)*<\/noscript>', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'<svg\b[^<]*(?:(?!<\/svg>)<[^<]*)*<\/svg>', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'<!--.*?-->', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'[\u200B-\u200D\uFEFF]', '', stripped)
    try:
        from markdownify import markdownify as md
        return md(stripped, heading_style="ATX", strip=["script", "style", "svg"])
    except ImportError:
        sel = Selector(stripped)
        return _safe(sel.get_all_text(strip=True))[:50000]


# ---------------------------------------------------------------------------
# Result saving
# ---------------------------------------------------------------------------

def save_result(data: dict, output_dir: Path, fmt: str, username: str = ""):
    name = username or _username_from_url(data.get("url", "unknown"))
    if fmt == "json":
        fp = _output_path(output_dir, name, "json")
        with fp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    elif fmt == "md":
        fp = _output_path(output_dir, name, "md")
        lines = [f"# {data.get('title', name)}", ""]
        if data.get("full_name"):
            lines.append(f"**Full Name:** {data['full_name']}")
        if data.get("followers"):
            lines.append(f"**Followers:** {data['followers']}")
        if data.get("bio"):
            lines.append(f"**Bio:** {data['bio']}")
        if data.get("description"):
            lines.append(f"**Description:** {data['description']}")
        text = data.get("text_content", data.get("content", ""))
        if text:
            lines.extend(["", "## Content", "", _clean_text(text)])
        with fp.open("w", encoding="utf-8") as f:
            f.write("\n".join(filter(None, lines)) + "\n")
    elif fmt == "html":
        fp = _output_path(output_dir, name, "html")
        content = data.get("content", data.get("text_content", ""))
        with fp.open("w", encoding="utf-8") as f:
            f.write(f"<!-- {data.get('url', '')} -->\n{content}")
    elif fmt == "txt":
        fp = _output_path(output_dir, name, "txt")
        with fp.open("w", encoding="utf-8") as f:
            f.write(data.get("text_content", data.get("content", "")))
    return fp


# ---------------------------------------------------------------------------
# CORE FETCHER FUNCTIONS
# ---------------------------------------------------------------------------

async def fetch_http(
    url: str,
    method: str = "GET",
    *,
    timeout: int = 30,
    proxy: str = "",
    headers: dict = None,
    cookies: dict = None,
    params: dict = None,
    data: Any = None,
    json_data: Any = None,
    impersonate: str = "chrome",
    stealthy_headers: bool = True,
    follow_redirects: bool = True,
    verify: bool = True,
    retries: int = 3,
    auth: tuple = None,
    http3: bool = False,
) -> Any:

    kwargs = dict(
        timeout=timeout,
        follow_redirects=follow_redirects,
        verify=verify,
        retries=retries,
        impersonate=impersonate,
        stealthy_headers=stealthy_headers,
    )
    if proxy:
        kwargs["proxy"] = proxy
    if headers:
        kwargs["headers"] = headers
    if cookies:
        kwargs["cookies"] = cookies
    if params:
        kwargs["params"] = params
    if data:
        kwargs["data"] = data
    if json_data:
        kwargs["json"] = json_data
    if auth:
        kwargs["auth"] = auth
    if http3:
        kwargs["http3"] = True

    method_map = {
        "GET": AsyncFetcher.get,
        "POST": AsyncFetcher.post,
        "PUT": AsyncFetcher.put,
        "DELETE": AsyncFetcher.delete,
    }
    fetcher_fn = method_map.get(method.upper(), AsyncFetcher.get)
    resp = await fetcher_fn(url, **kwargs)
    return resp


async def fetch_browser(
    url: str,
    *,
    timeout: int = 30000,
    proxy: str = "",
    headless: bool = True,
    disable_resources: bool = False,
    block_ads: bool = False,
    network_idle: bool = False,
    wait: int = 0,
    wait_selector: str = "",
    locale: str = "",
    real_chrome: bool = False,
    extra_headers: dict = None,
    cookies: list = None,
    useragent: str = "",
    dns_over_https: bool = False,
    capture_xhr: str = "",
) -> Any:
    kwargs = dict(
        timeout=timeout,
        headless=headless,
        disable_resources=disable_resources,
        block_ads=block_ads,
        network_idle=network_idle,
        wait=wait,
    )
    if proxy:
        kwargs["proxy"] = proxy
    if wait_selector:
        kwargs["wait_selector"] = wait_selector
    if locale:
        kwargs["locale"] = locale
    if real_chrome:
        kwargs["real_chrome"] = True
    if extra_headers:
        kwargs["extra_headers"] = extra_headers
    if cookies:
        kwargs["cookies"] = cookies
    if useragent:
        kwargs["useragent"] = useragent
    if dns_over_https:
        kwargs["dns_over_https"] = True
    if capture_xhr:
        kwargs["capture_xhr"] = capture_xhr
    resp = await DynamicFetcher.async_fetch(url, **kwargs)
    return resp


async def fetch_stealth(
    url: str,
    *,
    timeout: int = 30000,
    proxy: str = "",
    headless: bool = True,
    disable_resources: bool = False,
    block_ads: bool = False,
    network_idle: bool = False,
    wait: int = 0,
    wait_selector: str = "",
    locale: str = "",
    real_chrome: bool = False,
    extra_headers: dict = None,
    cookies: list = None,
    useragent: str = "",
    dns_over_https: bool = False,
    solve_cloudflare: bool = False,
    hide_canvas: bool = False,
    block_webrtc: bool = False,
    allow_webgl: bool = True,
    capture_xhr: str = "",
) -> Any:
    kwargs = dict(
        timeout=timeout,
        headless=headless,
        disable_resources=disable_resources,
        block_ads=block_ads,
        network_idle=network_idle,
        wait=wait,
        solve_cloudflare=solve_cloudflare,
        hide_canvas=hide_canvas,
        block_webrtc=block_webrtc,
        allow_webgl=allow_webgl,
    )
    if proxy:
        kwargs["proxy"] = proxy
    if wait_selector:
        kwargs["wait_selector"] = wait_selector
    if locale:
        kwargs["locale"] = locale
    if real_chrome:
        kwargs["real_chrome"] = True
    if extra_headers:
        kwargs["extra_headers"] = extra_headers
    if cookies:
        kwargs["cookies"] = cookies
    if useragent:
        kwargs["useragent"] = useragent
    if dns_over_https:
        kwargs["dns_over_https"] = True
    if capture_xhr:
        kwargs["capture_xhr"] = capture_xhr
    resp = await StealthyFetcher.async_fetch(url, **kwargs)
    return resp


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

async def capture_screenshot(
    url: str,
    output: Path,
    *,
    timeout: int = 30000,
    headless: bool = True,
    full_page: bool = True,
    wait: int = 1000,
    wait_selector: str = "",
    network_idle: bool = False,
    image_type: str = "png",
    quality: int = 80,
    proxy: str = "",
    real_chrome: bool = False,
) -> Path:
    async with async_playwright() as p:
        launch_kwargs = {"headless": headless}
        if real_chrome:
            launch_kwargs["channel"] = "chrome"
        browser = await p.chromium.launch(**launch_kwargs)
        context_kwargs = {}
        if proxy:
            context_kwargs["proxy"] = {"server": proxy}
        ctx = await browser.new_context(**context_kwargs)
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=timeout)
            if wait:
                await page.wait_for_timeout(wait)
            if network_idle:
                await page.wait_for_load_state("networkidle", timeout=timeout)
            opts = {"full_page": full_page, "type": image_type}
            if image_type == "jpeg":
                opts["quality"] = quality
            await page.screenshot(path=str(output), **opts)
        finally:
            await browser.close()
    return output


# ---------------------------------------------------------------------------
# Bulk batch runner
# ---------------------------------------------------------------------------

async def run_bulk(
    urls: list[str],
    mode: str = "http",
    *,
    concurrency: int = 5,
    timeout: int = 30,
    proxy: str = "",
    output_dir: Path = Path("output"),
    fmt: str = "json",
    instagram: bool = False,
    resume: bool = True,
    css_selector: str = "",
    extraction: str = "markdown",
    http_kwargs: dict = None,
    browser_kwargs: dict = None,
    stealth_kwargs: dict = None,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []
    seen = set()

    if resume:
        for f in output_dir.glob(f"*.{fmt}"):
            seen.add(f.stem)

    if console:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        )
        task = progress.add_task(f"[cyan]Scraping ({mode})...", total=len(urls))
        live_ctx = Live(progress, refresh_per_second=10)
    else:
        live_ctx = contextlib.nullcontext()

    http_kwargs = http_kwargs or {}
    browser_kwargs = browser_kwargs or {}
    stealth_kwargs = stealth_kwargs or {}

    with live_ctx:
        async def _worker(url: str) -> dict | None:
            username = _username_from_url(url)
            if resume and username in seen:
                if console:
                    progress.advance(task)
                try:
                    with (output_dir / f"{username}.{fmt}").open("r") as f:
                        prev = json.load(f) if fmt == "json" else {"url": url}
                    results.append(prev)
                    return prev
                except Exception:
                    pass

            last_err = None
            for attempt in range(3):  # retry up to 3 times
                try:
                    async with sem:
                        if mode == "browser":
                            resp = await fetch_browser(url, timeout=timeout * 1000, proxy=proxy, **browser_kwargs)
                        elif mode == "stealth":
                            resp = await fetch_stealth(url, timeout=timeout * 1000, proxy=proxy, **stealth_kwargs)
                        else:
                            resp = await fetch_http(url, timeout=timeout, proxy=proxy, **http_kwargs)
                    break
                except Exception as e:
                    last_err = e
                    if attempt < 2:
                        delay = 2 ** attempt  # 1s, 2s backoff
                        await asyncio.sleep(delay)
                    continue
            else:
                result = {"url": url, "status": 0, "error": str(last_err)[:300]}
                results.append(result)
                _save_extracted(result, output_dir, fmt, username)
                if console:
                    progress.advance(task)
                return result

            try:
                if "instagram.com" in url.lower() and not instagram:
                    data = parse_generic(resp, url)
                else:
                    data = _parse_for_url(resp, url)

                if extraction == "html":
                    data["content"] = extract_content(resp, css_selector)
                elif extraction == "markdown":
                    data["content"] = extract_markdown(resp, css_selector)
                elif extraction == "ai":
                    data["content"] = extract_ai_targeted(resp)
                else:
                    data["text_content"] = extract_text(resp, css_selector)

            except Exception as e:
                data = {"url": url, "status": getattr(resp, "status", 0), "parse_error": str(e)[:300]}

            results.append(data)
            _save_extracted(data, output_dir, fmt, username)
            if console:
                progress.advance(task)
            return data

        tasks = [_worker(url) for url in urls]
        await asyncio.gather(*tasks)

    return results


def _save_extracted(data: dict, output_dir: Path, fmt: str, username: str):
    try:
        save_result(data, output_dir, fmt, username)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI - Click interface
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

@click.group(context_settings=dict(help_option_names=["-h", "--help"]))
@click.version_option(version="1.3.0", prog_name="scrape")
def cli():
    """SCRAPLING TOOL v1.0  -  Social media & website scraper"""
    if not _HAS_FETCHERS:
        _echo("Scrapling not installed. Run: pip install scrapling[all]", "red")
        sys.exit(1)


# ----- RUN (one-shot) -----

@cli.command(context_settings=dict(ignore_unknown_options=False))
@click.argument("source", required=False, default="")
@click.option("-o", "--output", "-j", "--json", "output", default="results.json", help="Output JSON file path")
@click.option("-m", "--mode", type=click.Choice(["http", "browser", "stealth"]), default="http", help="Fetch mode")
@click.option("-c", "--concurrency", default=15, type=int, help="Max concurrent requests")
@click.option("-t", "--timeout", default=30, type=int, help="Request timeout in seconds")
@click.option("-p", "--proxy", help="Proxy URL")
@click.option("--no-clean", is_flag=True, help="Include raw content in output")
@click.option("--quiet", "-q", is_flag=True, help="Minimal output")
def run(source, output, mode, concurrency, timeout, proxy, no_clean, quiet):
    """SCRAPE URLs and export clean JSON in one step.

SOURCE can be a URL or file path. Auto-detects TikTok, Snapchat, Instagram."""
    if not source:
        try:
            import pyperclip
            clip = pyperclip.paste()
            if clip.strip():
                source = clip
                if not quiet:
                    _echo("Read URLs from clipboard", "cyan")
            else:
                _echo("No source provided and clipboard empty", "red")
                return
        except ImportError:
            _echo("No source provided. Install pyperclip for clipboard support: pip install pyperclip", "red")
            return

    urls = _read_urls(source)
    if not urls:
        _echo("No valid URLs found", "red")
        return

    out_path = Path(output)
    out_dir = out_path.parent if out_path.parent else Path(".")
    raw_dir = out_dir / out_path.stem
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not quiet:
        tiktok_c = sum(1 for u in urls if "tiktok.com" in u.lower())
        snap_c = sum(1 for u in urls if "snapchat.com" in u.lower())
        insta_c = sum(1 for u in urls if "instagram.com" in u.lower())
        other = len(urls) - tiktok_c - snap_c - insta_c
        _echo(f"Scraping {len(urls)} URLs ({tiktok_c} TikTok, {snap_c} Snapchat, {insta_c} Instagram, {other} other)", "cyan")

    raw_results = asyncio.run(_run_bulk(urls, mode, concurrency, timeout, proxy, not no_clean, quiet, raw_dir))

    if no_clean:
        out_path.write_text(json.dumps(raw_results, ensure_ascii=False, indent=2), encoding="utf-8")
        if not quiet:
            _echo(f"Saved to {out_path}", "green")
        return

    cleaned = []
    for r in raw_results:
        entry = {}
        platform = r.get("platform", "")
        entry["url"] = r.get("url", "")
        entry["username"] = r.get("username", "")
        entry["platform"] = platform
        entry["status"] = r.get("status", 0)
        entry["error"] = r.get("error", "")

        if platform == "snapchat":
            entry["subscribers"] = r.get("subscribers", "")
            entry["full_name"] = r.get("full_name", "")
            entry["bio"] = r.get("bio", "")
            entry["location"] = r.get("location", "")
            entry["last_updated"] = r.get("last_updated", "")
            entry["profile_pic"] = r.get("profile_pic", "")
        elif platform == "tiktok":
            for k in ("followers", "likes", "videos_count", "following", "full_name", "bio", "is_verified", "profile_pic", "hashtags"):
                entry[k] = r.get(k, "")
        elif platform == "instagram":
            for k in ("followers", "following", "posts_count", "videos_count", "avg_likes", "full_name", "bio", "hashtags", "mentions", "is_verified", "is_business", "external_url", "profile_pic"):
                entry[k] = r.get(k, "")
        elif platform == "youtube":
            for k in ("channel_name", "subscribers", "videos_count", "total_views", "description", "is_verified", "country", "profile_pic"):
                entry[k] = r.get(k, "")
        elif platform == "twitter":
            for k in ("full_name", "followers", "following", "posts_count", "likes_count", "bio", "is_verified", "location", "external_url", "profile_pic", "hashtags"):
                entry[k] = r.get(k, "")
        else:
            for k in ("title", "description"):
                entry[k] = r.get(k, "")
        cleaned.append(entry)

    if cleaned:
        out_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        with_data = sum(1 for r in cleaned if r.get("followers") or r.get("subscribers"))
        errs = sum(1 for r in cleaned if r.get("error"))
        if not quiet:
            _echo(f"Done {len(cleaned)} URLs | {len(urls)-errs} ok, {errs} errors | {with_data} with data", "green")
            _echo(f"Saved to {out_path}", "green")

    # Cleanup temp raw files
    import shutil
    shutil.rmtree(raw_dir, ignore_errors=True)


async def _run_bulk(urls, mode, concurrency, timeout, proxy, clean, quiet, raw_dir):
    """Core bulk scraping logic used by `run` command."""
    sem = asyncio.Semaphore(concurrency)
    results = []

    async def _worker(url: str):
        username = _username_from_url(url)
        try:
            async with sem:
                if mode == "browser":
                    resp = await fetch_browser(url, timeout=timeout * 1000, proxy=proxy)
                elif mode == "stealth":
                    resp = await fetch_stealth(url, timeout=timeout * 1000, proxy=proxy)
                else:
                    resp = await fetch_http(url, timeout=timeout, proxy=proxy)
        except Exception as e:
            result = {"url": url, "username": username, "status": 0, "error": str(e)[:300], "platform": "unknown"}
            results.append(result)
            _save_extracted(result, raw_dir, "json", username)
            return result

        try:
            data = _parse_for_url(resp, url)
        except Exception as e:
            data = {"url": url, "username": username, "status": getattr(resp, "status", 0), "error": f"parse: {e}", "platform": "unknown"}

        results.append(data)
        _save_extracted(data, raw_dir, "json", username)
        return data

    await asyncio.gather(*[_worker(url) for url in urls])
    return results


# ----- GET -----

@cli.command()
@click.argument("url")
@click.argument("output", required=False)
@click.option("-t", "--timeout", default=30, help="Timeout in seconds")
@click.option("-p", "--proxy", help="Proxy URL")
@click.option("-H", "--header", multiple=True, help="Header in 'Key: Value' format")
@click.option("--cookie", multiple=True, help="Cookie in 'name=value' format")
@click.option("--param", multiple=True, help="Query param in 'key=value' format")
@click.option("--impersonate", default="chrome", help="Browser to impersonate (chrome/firefox/safari/edge)")
@click.option("--no-stealthy-headers", is_flag=True, help="Disable stealthy browser headers")
@click.option("--no-follow-redirects", is_flag=True, help="Disable redirect following")
@click.option("--no-verify", is_flag=True, help="Disable SSL verification")
@click.option("--retries", default=3, help="Number of retries")
@click.option("--auth", help="Basic auth in 'user:pass' format")
@click.option("--http3", is_flag=True, help="Use HTTP/3")
@click.option("-s", "--css-selector", help="CSS selector to extract")
@click.option("--extraction", type=click.Choice(["html", "markdown", "text", "ai"]), default="markdown", help="Extraction format")
@click.option("--instagram", is_flag=True, help="Enable Instagram-aware parsing")
@click.option("--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json", help="Output format")
@click.option("-o", "--output-dir", default="output", help="Output directory")
def get(url, output, timeout, proxy, header, cookie, param, impersonate, no_stealthy_headers,
        no_follow_redirects, no_verify, retries, auth, http3, css_selector, extraction,
        instagram, output_format, output_dir):
    """HTTP GET a URL and save/extract content."""
    asyncio.run(_cmd_get(url, output, timeout, proxy, header, cookie, param, impersonate,
                         no_stealthy_headers, no_follow_redirects, no_verify, retries,
                         auth, http3, css_selector, extraction, instagram, output_format, output_dir))


async def _cmd_get(url, output, timeout, proxy, header, cookie, param, impersonate,
                   no_stealthy_headers, no_follow_redirects, no_verify, retries,
                   auth, http3, css_selector, extraction, instagram, output_format, output_dir):
    headers = dict(h.split(": ", 1) for h in header) if header else None
    cookies = dict(c.split("=", 1) for c in cookie) if cookie else None
    params = dict(p.split("=", 1) for p in param) if param else None
    auth_tuple = tuple(auth.split(":", 1)) if auth else None

    resp = await fetch_http(
        url, "GET",
        timeout=timeout, proxy=proxy, headers=headers, cookies=cookies,
        params=params, impersonate=impersonate,
        stealthy_headers=not no_stealthy_headers,
        follow_redirects=not no_follow_redirects,
        verify=not no_verify, retries=retries, auth=auth_tuple, http3=http3,
    )

    if "instagram.com" in url.lower() and not instagram:
        data = parse_generic(resp, url)
    else:
        data = _parse_for_url(resp, url)

    if extraction == "html":
        data["content"] = extract_content(resp, css_selector)
    elif extraction == "markdown":
        data["content"] = extract_markdown(resp, css_selector)
    elif extraction == "ai":
        data["content"] = extract_ai_targeted(resp)
    else:
        data["text_content"] = extract_text(resp, css_selector)

    if output:
        fp = Path(output)
        fp.parent.mkdir(parents=True, exist_ok=True)
        if fp.suffix == ".json":
            fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fp.suffix == ".md":
            lines = [f"# {data.get('title', '')}", f"**URL:** {url}", ""]
            if data.get("content"):
                lines.append(data["content"])
            fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif fp.suffix == ".txt":
            fp.write_text(data.get("content", data.get("text_content", "")), encoding="utf-8")
        elif fp.suffix == ".html":
            fp.write_text(data.get("content", ""), encoding="utf-8")
        _echo(f"Saved to {fp}", "green")
    else:
        out_dir = Path(output_dir)
        fp = save_result(data, out_dir, output_format)
        _echo(f"Saved to {fp}", "green")

    _echo(f"Status: {data.get('status')} | Title: {data.get('title', '')[:60]}", "cyan")


# ----- POST / PUT / DELETE -----

@cli.command()
@click.argument("url")
@click.argument("output", required=False)
@click.option("-d", "--data", help="Form data as 'key1=val1&key2=val2'")
@click.option("-j", "--json-data", help="JSON data as string")
@click.option("-t", "--timeout", default=30)
@click.option("-p", "--proxy")
@click.option("-H", "--header", multiple=True)
@click.option("--cookie", multiple=True)
@click.option("--param", multiple=True)
@click.option("--impersonate", default="chrome")
@click.option("--no-stealthy-headers", is_flag=True)
@click.option("--no-follow-redirects", is_flag=True)
@click.option("--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json")
@click.option("-o", "--output-dir", default="output")
@click.option("--extraction", type=click.Choice(["html", "markdown", "text", "ai"]), default="markdown")
@click.option("--instagram", is_flag=True)
def post(url, output, data, json_data, timeout, proxy, header, cookie, param,
         impersonate, no_stealthy_headers, no_follow_redirects,
         output_format, output_dir, extraction, instagram):
    """HTTP POST a URL."""
    asyncio.run(_cmd_data("POST", url, output, data, json_data, timeout, proxy, header,
                          cookie, param, impersonate, no_stealthy_headers,
                          no_follow_redirects, output_format, output_dir, extraction, instagram))


@cli.command()
@click.argument("url")
@click.argument("output", required=False)
@click.option("-d", "--data", help="Form data")
@click.option("-j", "--json-data", help="JSON data")
@click.option("-t", "--timeout", default=30)
@click.option("-p", "--proxy")
@click.option("-H", "--header", multiple=True)
@click.option("--cookie", multiple=True)
@click.option("--impersonate", default="chrome")
@click.option("--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json")
@click.option("-o", "--output-dir", default="output")
def put(url, output, data, json_data, timeout, proxy, header, cookie,
        impersonate, output_format, output_dir):
    """HTTP PUT a URL."""
    asyncio.run(_cmd_data("PUT", url, output, data, json_data, timeout, proxy, header,
                          cookie, None, impersonate, False, False,
                          output_format, output_dir, "markdown", False))


@cli.command()
@click.argument("url")
@click.argument("output", required=False)
@click.option("-t", "--timeout", default=30)
@click.option("-p", "--proxy")
@click.option("-H", "--header", multiple=True)
@click.option("--cookie", multiple=True)
@click.option("--impersonate", default="chrome")
@click.option("--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json")
@click.option("-o", "--output-dir", default="output")
def delete(url, output, timeout, proxy, header, cookie, impersonate, output_format, output_dir):
    """HTTP DELETE a URL."""
    asyncio.run(_cmd_data("DELETE", url, output, None, None, timeout, proxy, header,
                          cookie, None, impersonate, False, False,
                          output_format, output_dir, "markdown", False))


async def _cmd_data(method, url, output, data, json_data, timeout, proxy, header,
                    cookie, param, impersonate, no_stealthy_headers, no_follow_redirects,
                    output_format, output_dir, extraction, instagram):
    headers = dict(h.split(": ", 1) for h in header) if header else None
    cookies = dict(c.split("=", 1) for c in cookie) if cookie else None
    params = dict(p.split("=", 1) for p in param) if param else None
    json_payload = None
    if json_data:
        try:
            json_payload = json.loads(json_data)
        except json.JSONDecodeError as e:
            _echo(f"Invalid JSON data: {e}", "red")
            return

    resp = await fetch_http(
        url, method,
        timeout=timeout, proxy=proxy, headers=headers, cookies=cookies,
        params=params, impersonate=impersonate,
        stealthy_headers=not no_stealthy_headers,
        follow_redirects=not no_follow_redirects,
        data=data, json_data=json_payload,
    )

    # Auto-detect platform for parsing
    platform = _platform_of(url)
    if platform == "tiktok":
        data_dict = parse_tiktok(resp, url)
    elif platform == "instagram" and instagram:
        data_dict = parse_instagram(resp, url)
    elif platform == "snapchat":
        data_dict = parse_snapchat(resp, url)
    else:
        data_dict = parse_generic(resp, url)

    if extraction == "markdown":
        data_dict["content"] = extract_markdown(resp)
    else:
        data_dict["text_content"] = extract_text(resp)

    if output:
        fp = Path(output)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(data_dict, ensure_ascii=False, indent=2) if fp.suffix == ".json"
                      else data_dict.get("content", data_dict.get("text_content", "")), encoding="utf-8")
        _echo(f"Saved to {fp}", "green")
    else:
        fp = save_result(data_dict, Path(output_dir), output_format)
        _echo(f"Saved to {fp}", "green")

    status = data_dict.get("status", getattr(resp, "status", 0))
    _echo(f"Status: {status} | Title: {data_dict.get('title', '')[:60]}", "cyan")


# ----- BROWSER FETCH -----

@cli.command()
@click.argument("url")
@click.argument("output", required=False)
@click.option("-t", "--timeout", default=30, help="Timeout in seconds")
@click.option("-p", "--proxy")
@click.option("--no-headless", is_flag=True, help="Show browser window")
@click.option("--disable-resources", is_flag=True, help="Block images/fonts for speed")
@click.option("--block-ads", is_flag=True, help="Block ad/tracker domains")
@click.option("--network-idle", is_flag=True, help="Wait for network idle")
@click.option("--wait", type=int, default=0, help="Extra wait after load (ms)")
@click.option("--wait-selector", help="CSS selector to wait for")
@click.option("--locale", help="Browser locale")
@click.option("--real-chrome", is_flag=True, help="Use installed Chrome browser")
@click.option("--dns-over-https", is_flag=True, help="Route DNS via Cloudflare DoH")
@click.option("-H", "--extra-header", multiple=True, help="Extra headers")
@click.option("--cookie", multiple=True)
@click.option("--useragent", help="Custom user agent")
@click.option("-s", "--css-selector", help="CSS selector to extract")
@click.option("--extraction", type=click.Choice(["html", "markdown", "text", "ai"]), default="markdown")
@click.option("--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json")
@click.option("-o", "--output-dir", default="output")
def fetch(url, output, timeout, proxy, no_headless, disable_resources, block_ads,
          network_idle, wait, wait_selector, locale, real_chrome, dns_over_https,
          extra_header, cookie, useragent, css_selector, extraction,
          output_format, output_dir):
    """Fetch with browser automation (DynamicFetcher / Playwright)."""
    asyncio.run(_cmd_fetch(url, output, timeout, proxy, no_headless, disable_resources,
                           block_ads, network_idle, wait, wait_selector, locale, real_chrome,
                           dns_over_https, extra_header, cookie, useragent, css_selector,
                           extraction, output_format, output_dir, stealth=False))


@cli.command()
@click.argument("url")
@click.argument("output", required=False)
@click.option("-t", "--timeout", default=30, help="Timeout in seconds")
@click.option("-p", "--proxy")
@click.option("--no-headless", is_flag=True, help="Show browser window")
@click.option("--disable-resources", is_flag=True)
@click.option("--block-ads", is_flag=True)
@click.option("--network-idle", is_flag=True)
@click.option("--wait", type=int, default=0)
@click.option("--wait-selector")
@click.option("--locale")
@click.option("--real-chrome", is_flag=True)
@click.option("--dns-over-https", is_flag=True)
@click.option("--solve-cloudflare", is_flag=True, help="Auto-solve Cloudflare challenges")
@click.option("--hide-canvas", is_flag=True, help="Add noise to canvas fingerprinting")
@click.option("--block-webrtc", is_flag=True, help="Block WebRTC")
@click.option("--block-webgl", is_flag=True, help="Block WebGL")
@click.option("-H", "--extra-header", multiple=True)
@click.option("--cookie", multiple=True)
@click.option("--useragent")
@click.option("-s", "--css-selector")
@click.option("--extraction", type=click.Choice(["html", "markdown", "text", "ai"]), default="markdown")
@click.option("--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json")
@click.option("-o", "--output-dir", default="output")
def stealth(url, output, timeout, proxy, no_headless, disable_resources, block_ads,
            network_idle, wait, wait_selector, locale, real_chrome, dns_over_https,
            solve_cloudflare, hide_canvas, block_webrtc, block_webgl,
            extra_header, cookie, useragent, css_selector, extraction,
            output_format, output_dir):
    """Fetch with stealth anti-detection (StealthyFetcher)."""
    asyncio.run(_cmd_fetch(url, output, timeout, proxy, no_headless, disable_resources,
                           block_ads, network_idle, wait, wait_selector, locale, real_chrome,
                           dns_over_https, extra_header, cookie, useragent, css_selector,
                           extraction, output_format, output_dir, stealth=True,
                           solve_cloudflare=solve_cloudflare, hide_canvas=hide_canvas,
                           block_webrtc=block_webrtc, block_webgl=block_webgl))


async def _cmd_fetch(url, output, timeout, proxy, no_headless, disable_resources,
                     block_ads, network_idle, wait, wait_selector, locale, real_chrome,
                     dns_over_https, extra_header, cookie, useragent, css_selector,
                     extraction, output_format, output_dir, stealth=False, **stealth_kwargs):
    extra_headers = dict(h.split(": ", 1) for h in extra_header) if extra_header else None
    cookies_list = None
    if cookie:
        from playwright.async_api import SetCookieParam
        cookies_list = [SetCookieParam(name=c.split("=", 1)[0], value=c.split("=", 1)[1], url=url) for c in cookie]

    kw = dict(
        timeout=timeout * 1000, proxy=proxy,
        headless=not no_headless, disable_resources=disable_resources,
        block_ads=block_ads, network_idle=network_idle,
        wait=wait, wait_selector=wait_selector or "",
        locale=locale, real_chrome=real_chrome,
        dns_over_https=dns_over_https, extra_headers=extra_headers,
        cookies=cookies_list, useragent=useragent or "",
    )

    if stealth:
        kw.update(
            solve_cloudflare=stealth_kwargs.get("solve_cloudflare", False),
            hide_canvas=stealth_kwargs.get("hide_canvas", False),
            block_webrtc=stealth_kwargs.get("block_webrtc", False),
            allow_webgl=not stealth_kwargs.get("block_webgl", False),
        )
        resp = await fetch_stealth(url, **kw)
    else:
        resp = await fetch_browser(url, **kw)

    data = parse_generic(resp, url)

    if extraction == "html":
        data["content"] = extract_content(resp, css_selector)
    elif extraction == "markdown":
        data["content"] = extract_markdown(resp, css_selector)
    elif extraction == "ai":
        data["content"] = extract_ai_targeted(resp)
    else:
        data["text_content"] = extract_text(resp, css_selector)

    if output:
        fp = Path(output)
        fp.parent.mkdir(parents=True, exist_ok=True)
        if fp.suffix == ".json":
            fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fp.suffix == ".md":
            lines = [f"# {data.get('title', '')}", f"**URL:** {url}", ""]
            if data.get("content"):
                lines.append(data["content"])
            fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif fp.suffix == ".txt":
            fp.write_text(data.get("content", data.get("text_content", "")), encoding="utf-8")
        elif fp.suffix == ".html":
            fp.write_text(data.get("content", ""), encoding="utf-8")
        _echo(f"Saved to {fp}", "green")
    else:
        fp = save_result(data, Path(output_dir), output_format)
        _echo(f"Saved to {fp}", "green")

    _echo(f"Status: {data.get('status')} | Title: {data.get('title', '')[:60]}", "cyan")


# ----- BULK -----

@cli.command()
@click.argument("urls_source")
@click.option("-m", "--mode", type=click.Choice(["http", "browser", "stealth"]), default="http",
              help="Fetcher mode")
@click.option("-c", "--concurrency", default=5, help="Concurrent requests")
@click.option("-t", "--timeout", default=30, help="Timeout in seconds")
@click.option("-p", "--proxy", help="Proxy URL")
@click.option("-o", "--output-dir", default="output", help="Output directory")
@click.option("-f", "--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json")
@click.option("-s", "--css-selector", help="CSS selector to extract")
@click.option("--extraction", type=click.Choice(["html", "markdown", "text", "ai"]), default="markdown")
@click.option("--instagram", is_flag=True, help="Instagram-aware parsing")
@click.option("--no-resume", is_flag=True, help="Re-fetch already scraped URLs")
@click.option("--impersonate", default="chrome", help="Browser impersonation")
@click.option("--no-headless", is_flag=True, help="Show browser (browser/stealth modes)")
@click.option("--disable-resources", is_flag=True)
@click.option("--block-ads", is_flag=True)
@click.option("--network-idle", is_flag=True)
@click.option("--solve-cloudflare", is_flag=True, help="Solve Cloudflare (stealth mode)")
@click.option("--real-chrome", is_flag=True)
@click.option("--header", multiple=True, help="Headers 'Key: Value'")
@click.option("--cookie", multiple=True, help="Cookies 'name=value'")
def bulk(urls_source, mode, concurrency, timeout, proxy, output_dir, output_format,
         css_selector, extraction, instagram, no_resume, impersonate,
         no_headless, disable_resources, block_ads, network_idle,
         solve_cloudflare, real_chrome, header, cookie):
    """Bulk scrape multiple URLs from a file."""
    urls = _read_urls(urls_source)
    if not urls:
        _echo("No URLs found.", "red")
        return

    _echo(f"Scraping {len(urls)} URLs | mode: {mode} | concurrency: {concurrency}", "bold cyan")

    headers_dict = dict(h.split(": ", 1) for h in header) if header else None
    cookies_dict = dict(c.split("=", 1) for c in cookie) if cookie else None

    http_kwargs = dict(
        impersonate=impersonate,
        headers=headers_dict,
        cookies=cookies_dict,
    )

    browser_kwargs = dict(
        headless=not no_headless,
        disable_resources=disable_resources,
        block_ads=block_ads,
        network_idle=network_idle,
        extra_headers=headers_dict,
        real_chrome=real_chrome,
    )

    stealth_kwargs = dict(
        headless=not no_headless,
        disable_resources=disable_resources,
        block_ads=block_ads,
        network_idle=network_idle,
        extra_headers=headers_dict,
        real_chrome=real_chrome,
        solve_cloudflare=solve_cloudflare,
    )

    results = asyncio.run(run_bulk(
        urls, mode,
        concurrency=concurrency, timeout=timeout, proxy=proxy,
        output_dir=Path(output_dir), fmt=output_format,
        instagram=instagram, resume=not no_resume,
        css_selector=css_selector, extraction=extraction,
        http_kwargs=http_kwargs if mode == "http" else None,
        browser_kwargs=browser_kwargs if mode == "browser" else None,
        stealth_kwargs=stealth_kwargs if mode == "stealth" else None,
    ))

    ok = sum(1 for r in results if r.get("status") == 200 and not r.get("error"))
    _echo(f"Done: {ok}/{len(results)} successful", "green")


# ----- SCREENSHOT -----

@cli.command()
@click.argument("url")
@click.argument("output", required=False)
@click.option("-t", "--timeout", default=30, help="Timeout in seconds")
@click.option("--no-headless", is_flag=True)
@click.option("--full-page", is_flag=True, help="Full page screenshot")
@click.option("--wait", type=int, default=1000, help="Wait after load (ms)")
@click.option("--wait-selector", help="CSS selector to wait for")
@click.option("--network-idle", is_flag=True)
@click.option("--image-type", type=click.Choice(["png", "jpeg"]), default="png")
@click.option("--quality", type=int, default=80, help="JPEG quality (1-100)")
@click.option("--proxy")
@click.option("--real-chrome", is_flag=True, help="Use installed Chrome")
def screenshot(url, output, timeout, no_headless, full_page, wait, wait_selector,
               network_idle, image_type, quality, proxy, real_chrome):
    """Capture a screenshot of a web page."""
    out = Path(output or f"screenshot_{int(time.time())}.{image_type}")
    out.parent.mkdir(parents=True, exist_ok=True)

    _echo(f"Capturing screenshot of {url}...", "cyan")
    asyncio.run(capture_screenshot(
        url, out,
        timeout=timeout * 1000, headless=not no_headless,
        full_page=full_page, wait=wait, wait_selector=wait_selector or "",
        network_idle=network_idle, image_type=image_type,
        quality=quality, proxy=proxy or "", real_chrome=real_chrome,
    ))
    _echo(f"Screenshot saved to {out.absolute()}", "green")


# ----- INSTAGRAM DEDICATED COMMAND -----

@cli.command()
@click.argument("urls_source")
@click.option("-c", "--concurrency", default=5, help="Concurrent browser tabs")
@click.option("-o", "--output-dir", default="ig_data", help="Output directory")
@click.option("--login", is_flag=True, help="Open browser for manual Instagram login first")
@click.option("--no-headless", is_flag=True, help="Show browser window")
@click.option("--real-chrome", is_flag=True, help="Use installed Chrome browser")
@click.option("--timeout", default=30, help="Timeout per page in seconds")
@click.option("--proxy", help="Proxy URL")
@click.option("--session-dir", default=None, help="Custom browser profile directory")
@click.option("--csv", default="ig_results.csv", help="Output CSV path")
def instagram(urls_source, concurrency, output_dir, login, no_headless, real_chrome,
              timeout, proxy, session_dir, csv):
    """Scrape Instagram profiles using a stealth browser with login support.

    First run with --login to sign in, then reuse the session for bulk scraping.
    """
    urls = _read_urls(urls_source)
    if not urls:
        _echo("No URLs found.", "red")
        return

    urls = [u for u in urls if "instagram.com" in u.lower() or not u.startswith("http")]
    urls = [f"https://www.instagram.com/{u}/" if not u.startswith("http") else u for u in urls]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = Path(csv)

    session_path = session_dir or str(out_dir / "chrome_profile")
    headless = not no_headless

    if login:
        _echo("Opening browser for Instagram login. Please log in and close the browser when done.", "bold yellow")
        _echo(f"Session will be saved to: {session_path}", "cyan")
        asyncio.run(_instagram_login(session_path, proxy, timeout, real_chrome, headless))
        _echo("Login complete. Session saved. Now scraping...", "green")

    _echo(f"Scraping {len(urls)} Instagram profiles...", "bold cyan")
    _echo(f"  Session: {session_path}", "cyan")
    _echo(f"  Headless: {headless}", "cyan")
    _echo(f"  Concurrency: {concurrency}", "cyan")

    results = asyncio.run(_instagram_bulk(
        urls, concurrency, out_dir, timeout, proxy, session_path, headless, real_chrome
    ))

    _export_instagram_csv(results, csv_path)

    ok = sum(1 for r in results if r.get("status") == 200 and not r.get("error"))
    with_data = sum(1 for r in results if r.get("followers"))
    _echo(f"Done: {ok}/{len(results)} loaded, {with_data} with follower data", "green")
    _echo(f"CSV: {csv_path.absolute()}", "green")


async def _instagram_login(session_path: str, proxy: str, timeout: int, real_chrome: bool, headless: bool):
    """Open Instagram login page, let user log in manually, save session."""
    kwargs = dict(
        headless=False,
        user_data_dir=session_path,
        real_chrome=real_chrome,
        timeout=timeout * 1000,
        load_dom=True,
    )
    if proxy:
        kwargs["proxy"] = proxy

    async with AsyncStealthySession(**kwargs) as session:
        await session.fetch("https://www.instagram.com/accounts/login/")
        _echo("Please log in to Instagram in the browser window.", "bold yellow")
        _echo("After logging in, press Enter here to save session and continue...", "yellow")
        input()


async def _instagram_bulk(
    urls: list[str],
    concurrency: int,
    out_dir: Path,
    timeout: int,
    proxy: str,
    session_path: str,
    headless: bool,
    real_chrome: bool,
) -> list[dict]:
    """Bulk scrape Instagram profiles using a persistent browser session."""
    sem = asyncio.Semaphore(concurrency)
    results = []

    if console:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        )
        task = progress.add_task("[magenta]Instagram scraping...", total=len(urls))
        live_ctx = Live(progress, refresh_per_second=10)
    else:
        live_ctx = contextlib.nullcontext()

    session_kwargs = dict(
        user_data_dir=session_path,
        real_chrome=real_chrome,
        headless=headless,
        timeout=timeout * 1000,
        load_dom=True,
        network_idle=True,
        disable_resources=True,
    )
    if proxy:
        session_kwargs["proxy"] = proxy

    async with AsyncStealthySession(**session_kwargs) as session:
        with live_ctx:
            async def _worker(url: str):
                username = _username_from_url(url)
                async with sem:
                    try:
                        resp = await session.fetch(url)
                    except Exception as e:
                        data = {"url": url, "username": username, "status": 0, "error": str(e)[:300]}
                        results.append(data)
                        _save_extracted(data, out_dir, "json", username)
                        if console:
                            progress.advance(task)
                        return

                    try:
                        data = parse_instagram(resp, url)
                    except Exception as e:
                        data = {"url": url, "username": username, "status": getattr(resp, "status", 0), "error": f"parse: {e}"}

                    results.append(data)
                    _save_extracted(data, out_dir, "json", username)
                    if console:
                        progress.advance(task)

            tasks = [_worker(url) for url in urls]
            await asyncio.gather(*tasks)

    return results


def _export_instagram_csv(results: list[dict], csv_path: Path):
    """Export Instagram results to CSV."""
    import csv
    fields = [
        "username", "full_name", "followers", "following", "posts_count",
        "videos_count", "avg_likes", "bio", "hashtags", "mentions",
        "is_verified", "is_business", "business_category",
        "external_url", "profile_pic", "status", "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields + ["url"], extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {k: r.get(k, "") for k in fields}
            if isinstance(row.get("hashtags"), list):
                row["hashtags"] = " ".join(f"#{h}" for h in row["hashtags"])
            if isinstance(row.get("mentions"), list):
                row["mentions"] = " ".join(f"@{m}" for m in row["mentions"])
            if isinstance(row.get("recent_posts"), list):
                row["avg_likes"] = r.get("avg_likes", row.get("avg_likes", ""))
            row["url"] = r.get("url", "")
            writer.writerow(row)


# ----- SESSION -----

@cli.command()
@click.argument("urls_source")
@click.option("-t", "--timeout", default=30)
@click.option("-p", "--proxy")
@click.option("--impersonate", default="chrome")
@click.option("-o", "--output-dir", default="output")
@click.option("-f", "--output-format", type=click.Choice(["json", "md", "html", "txt"]), default="json")
@click.option("--extraction", type=click.Choice(["html", "markdown", "text", "ai"]), default="markdown")
@click.option("--instagram", is_flag=True)
def session(urls_source, timeout, proxy, impersonate, output_dir, output_format, extraction, instagram):
    """Fetch multiple URLs using a persistent HTTP session."""
    urls = _read_urls(urls_source)
    if not urls:
        _echo("No URLs found.", "red")
        return

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _echo(f"Session scraping {len(urls)} URLs...", "cyan")

    async def _run():
        async with FetcherSession(
            timeout=timeout, proxy=proxy or None,
            impersonate=impersonate, stealthy_headers=True,
        ) as sess:
            for url in urls:
                try:
                    resp = await sess.get(url)
                    if instagram and "instagram.com" in url.lower():
                        data = parse_instagram(resp, url)
                    else:
                        data = parse_generic(resp, url)
                    if extraction == "markdown":
                        data["content"] = extract_markdown(resp)
                    else:
                        data["text_content"] = extract_text(resp)
                    fp = save_result(data, out_dir, output_format)
                    _echo(f"  [{resp.status}] {url} -> {fp}", "green" if resp.status == 200 else "red")
                except Exception as e:
                    _echo(f"  [ERR] {url} -> {e}", "red")

    asyncio.run(_run())


# ----- SHELL -----

@cli.command()
@click.option("-c", "--code", help="Evaluate code and exit")
def shell(code):
    """Launch the interactive Scrapling shell."""
    if not _HAS_SHELL:
        _echo("Shell requires: pip install scrapling[shell]", "red")
        return
    if code:
        CustomShell().run(code)
    else:
        _echo("Starting Scrapling interactive shell...", "cyan")
        _echo("Pre-loaded: Fetcher, AsyncFetcher, DynamicFetcher, StealthyFetcher, Selector", "yellow")
        _echo("Shortcuts: get(), post(), fetch(), stealthy_fetch()", "yellow")
        CustomShell().run()


# ----- PARSE -----

@cli.command()
@click.argument("file")
@click.option("-s", "--css-selector", help="CSS selector to extract")
@click.option("--xpath", help="XPath selector")
@click.option("--extraction", type=click.Choice(["html", "markdown", "text", "ai", "json"]), default="text")
@click.option("-o", "--output", help="Save to file")
@click.option("--prettify", is_flag=True, help="Prettify the HTML output")
def parse(file, css_selector, xpath, extraction, output, prettify):
    """Parse a saved HTML file with selectors."""
    path = Path(file)
    if not path.exists():
        _echo(f"File not found: {file}", "red")
        return

    raw = path.read_text(encoding="utf-8")
    sel = Selector(raw)

    if css_selector and xpath:
        _echo("Use only one of --css-selector or --xpath", "red")
        return

    try:
        if css_selector:
            elements = sel.css(css_selector)
        elif xpath:
            elements = sel.xpath(xpath)
        else:
            elements = sel

        if extraction == "html":
            result = "\n\n".join(_safe(e.get()) for e in elements) if hasattr(elements, '__iter__') else _safe(elements.get())
        elif extraction == "markdown":
            html = "\n\n".join(_safe(e.get()) for e in elements) if hasattr(elements, '__iter__') else _safe(elements.get())
            try:
                from markdownify import markdownify as md
                result = md(html, heading_style="ATX")
            except ImportError:
                result = html
        elif extraction == "ai":
            result = extract_ai_targeted(sel)
        elif extraction == "json":
            items = []
            for e in elements:
                items.append({
                    "tag": _safe(e.tag),
                    "text": _safe(e.get_all_text(strip=True)) if hasattr(e, 'get_all_text') else _safe(str(e)),
                    "attributes": dict(e.attrib) if hasattr(e, 'attrib') else {},
                })
            result = json.dumps(items, ensure_ascii=False, indent=2)
        else:
            if hasattr(elements, '__iter__'):
                texts = [_safe(e.get_all_text(strip=True)) for e in elements if hasattr(e, 'get_all_text')]
                result = "\n---\n".join(filter(None, texts))
            else:
                result = _safe(elements.get_all_text(strip=True)) if hasattr(elements, 'get_all_text') else _safe(str(elements))

        if prettify:
            try:
                result = _safe(sel.prettify())
            except Exception:
                pass

        if output:
            Path(output).write_text(result, encoding="utf-8")
            _echo(f"Saved to {output}", "green")
        else:
            click.echo(result[:10000])

        count = len(elements) if hasattr(elements, '__len__') else 1
        _echo(f"\n[{count} element(s) extracted]", "cyan")

    except Exception as e:
        _echo(f"Parse error: {e}", "red")


# ----- SPIDER / CRAWL -----

@cli.command()
@click.argument("start_urls")
@click.option("-n", "--name", default="ultra_spider", help="Spider name")
@click.option("--allowed-domains", help="Comma-separated allowed domains")
@click.option("--concurrent", type=int, default=4, help="Max concurrent requests")
@click.option("--download-delay", type=float, default=0.0, help="Delay between requests (s)")
@click.option("--obey-robots-txt", is_flag=True, help="Respect robots.txt")
@click.option("--timeout", type=int, default=30, help="Request timeout")
@click.option("--max-pages", type=int, default=50, help="Max pages to crawl")
@click.option("-o", "--output-dir", default="crawl_output", help="Output directory")
@click.option("--resume", is_flag=True, help="Resume from checkpoint")
@click.option("--proxy")
@click.option("--impersonate", default="chrome")
@click.option("--stealthy", is_flag=True, help="Use stealth fetcher for spider")
def spider(start_urls, name, allowed_domains, concurrent, download_delay,
           obey_robots_txt, timeout, max_pages, output_dir, resume, proxy, impersonate, stealthy):
    """Crawl a website using the Scrapling Spider framework."""
    from scrapling.spiders import CrawlResult, Request, Spider

    urls = _read_urls(start_urls)
    if not urls:
        _echo("No start URLs provided.", "red")
        return

    domains = set()
    if allowed_domains:
        domains = set(d.strip() for d in allowed_domains.split(","))

    class UltraSpider(Spider):
        name = name
        start_urls = urls
        allowed_domains = domains
        concurrent_requests = concurrent
        download_delay = download_delay
        robots_txt_obey = obey_robots_txt
        development_mode = not resume

        async def parse(self, response):
            title = _safe(response.css("title::text").get() or "")
            text = _safe(response.get_all_text(strip=True))[:2000]
            links = response.css("a::attr(href)").getall() or []

            yield {
                "url": response.url,
                "status": response.status,
                "title": title,
                "text_snippet": text,
                "links_count": len(links),
            }

            for link in links:
                if len(self.stats.total_requests) >= max_pages:
                    return
                yield Request(link, callback=self.parse)

    _echo(f"Starting spider: {name}", "cyan")
    _echo(f"  Start URLs: {len(urls)}", "cyan")
    _echo(f"  Max pages: {max_pages}", "cyan")
    _echo(f"  Concurrent: {concurrent}", "cyan")

    async def _run_spider():
        spider_instance = UltraSpider()
        result: CrawlResult = await spider_instance.start()
        return result

    result = asyncio.run(_run_spider())

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save stats
    stats_path = out_dir / "crawl_stats.json"
    stats_path.write_text(
        json.dumps(result.stats.to_dict() if hasattr(result.stats, 'to_dict') else {"items": len(result.items)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Save items
    items_path = out_dir / "crawl_items.jsonl"
    with items_path.open("w", encoding="utf-8") as f:
        for item in result.items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    info = result.stats.to_dict() if hasattr(result.stats, 'to_dict') else {}
    _echo("\nSpider complete:", "bold green")
    _echo(f"  Items: {len(result.items)}", "green")
    _echo(f"  Stats: {json.dumps(info, indent=2)}", "cyan")
    _echo(f"  Saved to {out_dir.absolute()}", "green")


# ----- MCP Server -----

@cli.command()
@click.option("--http", is_flag=True, help="Run in HTTP mode instead of stdio")
@click.option("--host", default="0.0.0.0", help="HTTP host")
@click.option("--port", default=8000, type=int, help="HTTP port")
def mcp(http, host, port):
    """Run the Scrapling MCP AI server."""
    if not _HAS_MCP:
        _echo("MCP server requires: pip install scrapling[ai]", "red")
        return
    _echo("Starting Scrapling MCP server...", "cyan")
    server = ScraplingMCPServer()
    if http:
        _echo(f"HTTP mode on {host}:{port}", "green")
        server.run_http(host=host, port=port)
    else:
        _echo("STDIO mode", "green")
        server.run()


# ----- INSTALL -----

@cli.command()
@click.option("-f", "--force", is_flag=True, help="Force reinstall")
def install(force):
    """Install Scrapling's fetcher dependencies (Playwright browsers)."""
    try:
        from scrapling import Fetcher  # noqa: F401  (availability probe)
    except ImportError:
        _echo("Scrapling not installed. Run: pip install scrapling[all]", "red")
        return

    import shutil
    import subprocess

    scripts_dir = Path(sys.executable).parent
    scrapling_cli = scripts_dir / ("scrapling.exe" if os.name == "nt" else "scrapling")
    if scrapling_cli.exists():
        cmd = [str(scrapling_cli), "install"]
    else:
        found = shutil.which("scrapling")
        cmd = [found, "install"] if found else [sys.executable, "-m", "scrapling.cli", "install"]
    if force:
        cmd.append("-f")
    _echo("Installing fetcher dependencies...", "cyan")
    subprocess.run(cmd, check=True)
    _echo("Done!", "green")


# ----- EXPORT CSV -----

@cli.command()
@click.argument("input_dir", default="output")
@click.argument("output_csv", default="combined.csv")
@click.option("--flatten", is_flag=True, help="Flatten nested JSON fields")
def export(input_dir, output_csv, flatten):
    """Merge all JSON results in a directory into a single CSV."""
    import csv

    dir_path = Path(input_dir)
    if not dir_path.is_dir():
        _echo(f"Directory not found: {input_dir}", "red")
        return

    json_files = list(dir_path.glob("*.json"))
    if not json_files:
        _echo(f"No JSON files found in {input_dir}", "red")
        return

    all_data = []
    seen_fields = set()
    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                all_data.append(data)
                seen_fields.update(data.keys())
        except Exception as e:
            _echo(f"Error reading {jf.name}: {e}", "red")

    if not all_data:
        _echo("No valid data found.", "red")
        return

    fields = sorted(seen_fields)
    out_path = Path(output_csv)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_data)

    _echo(f"Exported {len(all_data)} rows, {len(fields)} columns to {out_path}", "green")


# ----- WEB SERVER -----

def _parse_for_url(resp, url: str) -> dict:
    """Dispatch to the right parser based on the URL's platform."""
    platform = _platform_of(url)
    if platform == "tiktok":
        return parse_tiktok(resp, url)
    if platform == "instagram":
        return parse_instagram(resp, url)
    if platform == "snapchat":
        return parse_snapchat(resp, url)
    if platform == "youtube":
        return parse_youtube(resp, url)
    if platform == "twitter":
        return parse_twitter(resp, url)
    return parse_generic(resp, url)


async def _scrape_one(url: str, mode: str, timeout: int, sem, *, retries: int = 2,
                      auto_escalate: bool = False, use_cache: bool = True, options: dict = None) -> dict:
    """Fetch + parse a single URL with smart retries, optional mode escalation, and caching.

    Always returns a dict (never raises) so it can drive both the batch and
    streaming endpoints. Includes `response_time` (seconds) and `platform`.

    Args:
        auto_escalate: If True and http mode fails, automatically try browser then stealth.
        use_cache: If True, return cached results when available.
        options: Advanced options dictionary for browser/HTTP/stealth fetchers.
    """
    import asyncio
    import random
    url = _canonical_social_url(url)

    # Validate URL first
    valid, result = _validate_url(url)
    if not valid:
        return {
            "url": url, "username": "", "platform": _platform_of(url),
            "status": 0, "error": result, "response_time": 0, "attempts": 0,
        }
    url = result

    username = _username_from_url(url)
    platform = _platform_of(url)

    # Check cache
    if use_cache:
        cached = _cache_get(url, mode)
        if cached is not None:
            cached = _canonical_profile_fields(cached)
            cached["from_cache"] = True
            return cached

    start = time.perf_counter()
    last_err = None
    resp = None

    # Extract options
    opt = options or {}
    proxy = opt.get("proxy", "")
    headless = opt.get("headless", True)
    disable_resources = opt.get("disable_resources", False)
    block_ads = opt.get("block_ads", False)
    network_idle = opt.get("network_idle", False)
    wait = opt.get("wait", 0)
    wait_selector = opt.get("wait_selector", "")
    locale = opt.get("locale", "")
    real_chrome = opt.get("real_chrome", False)
    useragent = opt.get("useragent", "")
    dns_over_https = opt.get("dns_over_https", False)

    solve_cloudflare = opt.get("solve_cloudflare", False)
    hide_canvas = opt.get("hide_canvas", False)
    block_webrtc = opt.get("block_webrtc", False)
    allow_webgl = opt.get("allow_webgl", True)

    method = opt.get("method", "GET")
    headers = opt.get("headers")
    data_payload = opt.get("data")
    json_payload = opt.get("json_data")
    impersonate = opt.get("impersonate", "chrome")
    stealthy_headers = opt.get("stealthy_headers", True)
    follow_redirects = opt.get("follow_redirects", True)
    verify = opt.get("verify", True)
    http3 = opt.get("http3", False)

    # Build escalation chain: if auto_escalate, try progressively stronger modes
    modes_to_try = [mode]
    if auto_escalate and mode == "http":
        modes_to_try = ["http", "browser", "stealth"]
    elif auto_escalate and mode == "browser":
        modes_to_try = ["browser", "stealth"]

    total_attempts = 0
    for current_mode in modes_to_try:
        for attempt in range(retries + 1):
            total_attempts += 1
            try:
                async with sem:
                    if current_mode == "browser":
                        resp = await fetch_browser(
                            url,
                            timeout=timeout * 1000,
                            proxy=proxy,
                            headless=headless,
                            disable_resources=disable_resources,
                            block_ads=block_ads,
                            network_idle=network_idle,
                            wait=wait,
                            wait_selector=wait_selector,
                            locale=locale,
                            real_chrome=real_chrome,
                            extra_headers=headers,
                            useragent=useragent,
                            dns_over_https=dns_over_https
                        )
                    elif current_mode == "stealth":
                        resp = await fetch_stealth(
                            url,
                            timeout=timeout * 1000,
                            proxy=proxy,
                            headless=headless,
                            disable_resources=disable_resources,
                            block_ads=block_ads,
                            network_idle=network_idle,
                            wait=wait,
                            wait_selector=wait_selector,
                            locale=locale,
                            real_chrome=real_chrome,
                            extra_headers=headers,
                            useragent=useragent,
                            dns_over_https=dns_over_https,
                            solve_cloudflare=solve_cloudflare,
                            hide_canvas=hide_canvas,
                            block_webrtc=block_webrtc,
                            allow_webgl=allow_webgl
                        )
                    else:
                        resp = await fetch_http(
                            url,
                            method=method,
                            timeout=timeout,
                            proxy=proxy,
                            headers=headers,
                            data=data_payload,
                            json_data=json_payload,
                            impersonate=impersonate,
                            stealthy_headers=stealthy_headers,
                            follow_redirects=follow_redirects,
                            verify=verify,
                            http3=http3
                        )

                # Check if response indicates a login wall or block that might benefit from escalation
                status = getattr(resp, "status", 0)
                if status in (403, 429) and auto_escalate and current_mode != modes_to_try[-1]:
                    _logger.debug(f"Got {status} in {current_mode} mode, escalating...")
                    resp = None
                    break  # Try next mode

                break  # Success
            except Exception as e:
                last_err = e
                _logger.debug(f"Attempt {attempt + 1}/{retries + 1} ({current_mode}) failed: {e}")
                if attempt < retries:
                    # Exponential backoff with jitter
                    delay = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
        if resp is not None:
            break

    if resp is None:
        return {
            "url": url, "username": username, "platform": platform,
            "status": 0, "error": str(last_err)[:300],
            "response_time": round(time.perf_counter() - start, 2),
            "attempts": total_attempts,
        }

    try:
        data = _parse_for_url(resp, url)
    except Exception as e:
        data = {
            "url": url, "username": username, "platform": platform,
            "status": getattr(resp, "status", 0), "error": f"parse: {e}"[:300],
        }
    # Normalize and optionally augment with structured profile extraction
    data.setdefault("platform", platform)
    data.setdefault("username", username)
    data.setdefault("url", url)
    data["response_time"] = round(time.perf_counter() - start, 2)
    data = _augment_with_profile(data, resp, url)
    data = _canonical_profile_fields(data)

    # Store in cache
    if use_cache:
        _cache_set(url, mode, data)

    return data


def _augment_with_profile(data: dict, resp, url: str) -> dict:
    """Add structured profile caption + recent posts using the new parser layer.

    This runs on the response bytes we already have, so it is cheap and never
    hits the network. It fills in ``bio``/``description`` and ``recent_posts``
    when the platform-specific parser left them empty.
    """
    try:
        body = getattr(resp, "body", None) or getattr(resp, "content", b"")
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not body:
            return data
        profile = extract_profile(body, url=url, max_recent=5)
        if profile.main_caption:
            if not data.get("bio"):
                data["bio"] = profile.main_caption
            if not data.get("description"):
                data["description"] = profile.main_caption
        if profile.display_name and not data.get("full_name"):
            data["full_name"] = profile.display_name
        if profile.avatar and not data.get("profile_pic"):
            data["profile_pic"] = profile.avatar
            data["profile_pic_hd"] = profile.avatar
        if profile.recent_posts and not data.get("recent_posts"):
            data["recent_posts"] = [
                {
                    "kind": p.kind,
                    "caption": p.caption,
                    "url": p.url,
                    "posted_at": p.posted_at,
                }
                for p in profile.recent_posts
            ]
        data.setdefault("_profile_source", profile.source)
    except Exception as e:
        _logger.debug(f"Profile augmentation failed for {url}: {e}")
    return data


def _canonical_profile_fields(data: dict) -> dict:
    """Add the stable profile schema used by tables, cards, popups, and exports."""
    result = dict(data or {})
    profile_url = str(result.get("profile_url") or result.get("url") or "").strip()
    platform = str(result.get("platform") or _platform_of(profile_url) or "").lower()
    biography = str(
        result.get("biography")
        or result.get("bio")
        or result.get("description")
        or ""
    ).strip()
    full_name = str(
        result.get("full_name")
        or result.get("channel_name")
        or result.get("display_name")
        or result.get("title")
        or ""
    ).strip()
    username = str(result.get("username") or _username_from_url(profile_url) or "").strip().lstrip("@")

    text_blob = "\n".join(
        str(value)
        for value in (
            biography,
            result.get("description", ""),
            result.get("text_content", ""),
            result.get("external_url", ""),
        )
        if value
    )
    emails = result.get("emails")
    if not isinstance(emails, list):
        emails = re.findall(
            r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])",
            text_blob,
        )
    phones = result.get("phone_numbers") or result.get("phones")
    if not isinstance(phones, list):
        phones = []
        for match in re.findall(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)", text_blob):
            digits = re.sub(r"\D", "", match)
            if 8 <= len(digits) <= 15:
                phones.append(match.strip())

    raw_links: list[str] = []
    for value in (
        profile_url,
        result.get("external_url", ""),
        *(result.get("links") if isinstance(result.get("links"), list) else []),
    ):
        if isinstance(value, str):
            raw_links.extend(re.findall(r"https?://[^\s<>'\"\])]+", value))
    raw_links.extend(re.findall(r"https?://[^\s<>'\"\])]+", text_blob))
    links: list[str] = []
    for link in raw_links:
        cleaned = html.unescape(link).rstrip(".,;:")
        if cleaned and cleaned not in links:
            links.append(cleaned)

    classified = {
        "tiktok_links": [],
        "snapchat_links": [],
        "twitter_links": [],
        "instagram_links": [],
        "other_links": [],
    }
    for link in links:
        host = _urlparse(link).netloc.lower()
        if "tiktok.com" in host:
            bucket = "tiktok_links"
        elif "snapchat.com" in host:
            bucket = "snapchat_links"
        elif "twitter.com" in host or host == "x.com" or host.endswith(".x.com"):
            bucket = "twitter_links"
        elif "instagram.com" in host:
            bucket = "instagram_links"
        else:
            bucket = "other_links"
        classified[bucket].append(link)

    recent_posts = result.get("recent_posts")
    view_counts = []
    if isinstance(recent_posts, list):
        for post in recent_posts:
            if not isinstance(post, dict):
                continue
            value = post.get("views") or post.get("view_count") or post.get("play_count")
            parsed = _to_int_count(value)
            if parsed > 0:
                view_counts.append(parsed)
    avg_views = result.get("avg_views", "")
    if not avg_views and view_counts:
        avg_views = round(sum(view_counts) / len(view_counts))

    location = str(result.get("location") or "").strip()
    country = str(result.get("country") or "").strip()
    city = str(result.get("city") or "").strip()
    if location and not city:
        parts = [part.strip() for part in re.split(r"[,|·]", location) if part.strip()]
        if parts:
            city = parts[0]
        if len(parts) > 1 and not country:
            country = parts[-1]

    private_match = re.search(
        r'"(?:is_private|privateAccount)"\s*:\s*(true|false)',
        text_blob + "\n" + str(result.get("_raw_profile_flags", "")),
        re.I,
    )
    is_private = bool(result.get("is_private", False))
    if private_match:
        is_private = private_match.group(1).lower() == "true"

    result.update({
        "profile_url": profile_url,
        "full_name": full_name,
        "username": username,
        "profile_category": str(
            result.get("profile_category")
            or result.get("business_category")
            or result.get("category")
            or ""
        ),
        "license": str(result.get("license") or ""),
        "biography": biography,
        "followers": result.get("followers") or result.get("subscribers") or "",
        "following": result.get("following") or "",
        "likes": result.get("likes") or result.get("likes_count") or "",
        "avg_views": avg_views,
        "emails": list(dict.fromkeys(str(value) for value in emails if value)),
        "phone_numbers": list(dict.fromkeys(str(value) for value in phones if value)),
        "country": country,
        "city": city,
        "is_business": bool(result.get("is_business", False)),
        "is_private": is_private,
        **classified,
    })
    result.setdefault("platform", platform)
    return result


def _scrape_stats(results: list[dict]) -> dict:
    ok = sum(1 for r in results if r.get("status") == 200 and not r.get("error"))
    errs = sum(1 for r in results if r.get("error") or r.get("status") != 200)
    with_data = sum(1 for r in results if r.get("followers") or r.get("subscribers"))
    times = [r["response_time"] for r in results if isinstance(r.get("response_time"), (int, float))]
    return {
        "total": len(results), "ok": ok, "errors": errs, "withData": with_data,
        "avgTime": round(sum(times) / len(times), 2) if times else 0,
    }


# ---------------------------------------------------------------------------
# Influencer discovery (search-engine driven profile finding + ranking)
# ---------------------------------------------------------------------------

# Path segments that are NOT user profiles on each platform.
_IG_RESERVED = {
    "p", "reel", "reels", "explore", "tags", "stories", "accounts", "about",
    "directory", "developer", "legal", "privacy", "web", "emails", "challenge",
    "ar", "tv", "session", "lite", "graphql", "api",
}
_TW_RESERVED = {
    "home", "search", "explore", "i", "intent", "hashtag", "share", "messages",
    "notifications", "settings", "login", "signup", "tos", "privacy", "about",
    "compose", "logout", "account", "personalization", "who_to_follow", "status",
}
_YT_RESERVED = {
    "watch", "results", "feed", "playlist", "shorts", "hashtag", "gaming",
    "premium", "about", "howyoutubeworks", "redirect", "embed", "live",
}


_LOCAL_PROFILE_CACHE: list[dict] | None = None


def _to_int_count(val) -> int:
    """Parse a follower count ('1.2M', '161600000', '161.6K', '12,345') -> int."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().replace(",", "").replace(" ", "")
    if not s:
        return 0
    m = re.match(r"^([\d.]+)([kmbKMB]?)$", s)
    if not m:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0
    try:
        num = float(m.group(1))
    except ValueError:
        return 0
    mult = {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}[m.group(2).lower()]
    return int(num * mult)


def _normalize_local_profile(data: dict, platform: str = "") -> dict:
    row = dict(data)
    url = _canonical_social_url(str(row.get("url", "")))
    platform = platform or row.get("platform") or _platform_of(url)
    row["url"] = url
    row["platform"] = platform
    row["username"] = str(row.get("username") or _username_from_url(url)).lstrip("@")
    status = row.get("status", 200)
    try:
        status = int(status)
    except Exception:
        status = 200
    row["status"] = status
    if str(row.get("is_verified", "")).lower() == "true":
        row["is_verified"] = True
    elif str(row.get("is_verified", "")).lower() == "false":
        row["is_verified"] = False
    row["search_source"] = row.get("search_source", "local_cache")
    row["search_only"] = True
    return _enrich_counts(row)


def _load_local_profiles() -> list[dict]:
    """Load already-scraped profile data bundled in the workspace."""
    global _LOCAL_PROFILE_CACHE
    if _LOCAL_PROFILE_CACHE is not None:
        return _LOCAL_PROFILE_CACHE

    root = Path(__file__).parent
    profiles: list[dict] = []

    for path, platform in (
        (root / "tiktok_results.json", "tiktok"),
        (root / "snapchat_results.json", "snapchat"),
        (root / "mix_results.json", ""),
    ):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload if isinstance(payload, list) else payload.get("results", [])
            for row in rows or []:
                if isinstance(row, dict) and row.get("url"):
                    profiles.append(_normalize_local_profile(row, platform))
        except Exception:
            pass

    for folder in ("instagram_data", "instagram_pages_structured"):
        base = root / folder
        if not base.exists():
            continue
        for jf in base.glob("*.json"):
            try:
                row = json.loads(jf.read_text(encoding="utf-8"))
                if isinstance(row, dict) and row.get("url"):
                    profiles.append(_normalize_local_profile(row, "instagram"))
            except Exception:
                continue

    seen, deduped = set(), []
    for row in profiles:
        key = (row.get("platform", ""), str(row.get("url", "")).lower().rstrip("/"))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    _LOCAL_PROFILE_CACHE = deduped
    return deduped


def _local_profile_hits(keyword: str, platforms: list[str], *, location: str = "",
                        per_platform: int = 15, target: str = "accounts") -> dict[str, list[dict]]:
    if target != "accounts":
        return {}
    terms = [t.lower() for t in re.findall(r"[\w\u0600-\u06FF]{2,}", keyword or "")]
    loc_terms = [t.lower() for t in re.findall(r"[\w\u0600-\u06FF]{2,}", location or "")]
    out: dict[str, list[dict]] = {p: [] for p in platforms}
    scored: dict[str, list[tuple[int, dict]]] = {p: [] for p in platforms}

    for row in _load_local_profiles():
        platform = row.get("platform", "other")
        if platform not in scored:
            continue
        blob = " ".join(str(row.get(k, "")) for k in (
            "username", "full_name", "title", "bio", "description", "location",
            "business_category", "hashtags",
        )).lower()
        if terms:
            matched_terms = sum(1 for t in terms if t in blob)
            if not matched_terms:
                continue
        else:
            matched_terms = 1
        if loc_terms and not any(t in blob for t in loc_terms):
            continue
        phrase_bonus = 4 if keyword and keyword.lower() in blob else 0
        score = matched_terms * 10 + phrase_bonus + min(_to_int_count(row.get("followers") or row.get("subscribers")) // 100000, 20)
        scored[platform].append((score, row))

    for platform, rows in scored.items():
        rows.sort(key=lambda item: (item[0], _to_int_count(item[1].get("followers") or item[1].get("subscribers"))), reverse=True)
        out[platform] = [
            {"url": r["url"], "title": r.get("title") or r.get("full_name") or r.get("username", ""),
             "snippet": r.get("bio") or r.get("description") or "", "source": "local_cache", "local_data": r}
            for _, r in rows[:per_platform]
        ]
    return out


TARGETS = ("accounts", "videos", "posts", "stories", "hashtags")

# site-scoped search prefixes per (platform, target). Falls back to the bare
# domain when a target has no dedicated path scope.
_TARGET_SCOPES = {
    "tiktok":    {"accounts": "site:tiktok.com", "videos": "site:tiktok.com",
                  "posts": "site:tiktok.com", "stories": "site:tiktok.com",
                  "hashtags": "site:tiktok.com/tag"},
    "instagram": {"accounts": "site:instagram.com", "videos": "site:instagram.com/reel",
                  "posts": "site:instagram.com/p", "stories": "site:instagram.com/stories",
                  "hashtags": "site:instagram.com/explore/tags"},
    "youtube":   {"accounts": "site:youtube.com", "videos": "site:youtube.com/watch",
                  "posts": "site:youtube.com/watch", "stories": "site:youtube.com/shorts",
                  "hashtags": "site:youtube.com/hashtag"},
    "twitter":   {"accounts": "(site:twitter.com OR site:x.com)",
                  "videos": "(site:twitter.com OR site:x.com)",
                  "posts": "(site:twitter.com OR site:x.com)",
                  "stories": "(site:twitter.com OR site:x.com)",
                  "hashtags": "(site:twitter.com/hashtag OR site:x.com/hashtag)"},
    "snapchat":  {"accounts": "site:snapchat.com/add", "videos": "site:snapchat.com/add",
                  "posts": "site:snapchat.com/add", "stories": "site:snapchat.com/add",
                  "hashtags": "site:snapchat.com/add"},
}


def _build_search_query(platform: str, keyword: str, location: str = "",
                        target: str = "accounts", mentions: str = "") -> str:
    if target not in TARGETS:
        target = "accounts"
    scopes = _TARGET_SCOPES.get(platform, {})
    base = scopes.get(target) or scopes.get("accounts") or ""
    loc = f" {location}" if location else ""
    mention_q = f" @{mentions.lstrip('@')}" if mentions else ""
    return f"{base} {keyword}{mention_q}{loc}".strip()


def _content_filter(platform: str, url: str, target: str) -> str | None:
    """Validate/canonicalize a video, post, or hashtag URL for the platform."""
    from urllib.parse import parse_qs, urlparse
    url = _canonical_social_url(url)
    try:
        p = urlparse(url)
    except Exception:
        return None
    host = (p.netloc or "").lower().replace("www.", "")
    path = (p.path or "").strip("/")

    if platform == "tiktok":
        if "tiktok.com" not in host:
            return None
        if target == "hashtags":
            m = re.match(r"tag/([^/]+)$", path)
            return f"https://www.tiktok.com/tag/{m.group(1)}" if m else None
        m = re.match(r"@([\w.\-]+)/video/(\d+)", path)
        return f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}" if m else None

    if platform == "instagram":
        if "instagram.com" not in host:
            return None
        if target == "hashtags":
            m = re.match(r"explore/tags/([^/]+)", path)
            return f"https://www.instagram.com/explore/tags/{m.group(1)}/" if m else None
        if target == "stories":
            m = re.match(r"stories/([\w.\-]+)(?:/(\d+))?", path)
            if m:
                suffix = f"/{m.group(2)}" if m.group(2) else ""
                return f"https://www.instagram.com/stories/{m.group(1)}{suffix}/"
        if target == "videos":
            m = re.match(r"reels?/([\w\-]+)", path)
            return f"https://www.instagram.com/reel/{m.group(1)}/" if m else None
        m = re.match(r"p/([\w\-]+)", path)  # posts
        return f"https://www.instagram.com/p/{m.group(1)}/" if m else None

    if platform == "youtube":
        if "youtube.com" not in host and "youtu.be" not in host:
            return None
        if target == "hashtags":
            m = re.match(r"hashtag/([^/]+)", path)
            return f"https://www.youtube.com/hashtag/{m.group(1)}" if m else None
        if "youtu.be" in host:
            vid = path.split("/")[0]
            return f"https://www.youtube.com/watch?v={vid}" if vid else None
        m = re.match(r"shorts/([\w\-]+)", path)
        if m:
            return f"https://www.youtube.com/shorts/{m.group(1)}"
        if path == "watch":
            vid = parse_qs(p.query).get("v", [""])[0]
            return f"https://www.youtube.com/watch?v={vid}" if vid else None
        return None

    if platform == "twitter":
        if "twitter.com" not in host and "x.com" not in host:
            return None
        if target == "hashtags":
            m = re.match(r"hashtag/([^/]+)", path)
            return f"https://twitter.com/hashtag/{m.group(1)}" if m else None
        m = re.match(r"([A-Za-z0-9_]+)/status/(\d+)", path)  # posts / videos
        return f"https://twitter.com/{m.group(1)}/status/{m.group(2)}" if m else None

    if platform == "snapchat":
        if "snapchat.com" not in host:
            return None
        spotlight = re.match(r"spotlight/([A-Za-z0-9._-]+)", path)
        if spotlight:
            return f"https://www.snapchat.com/spotlight/{spotlight.group(1)}"
        story = re.match(r"add/([A-Za-z0-9._-]+)(?:/([A-Za-z0-9._-]+))?", path)
        if story:
            suffix = f"/{story.group(2)}" if story.group(2) else ""
            return f"https://www.snapchat.com/add/{story.group(1)}{suffix}"
        return None

    return None


def _url_filter(platform: str, url: str, target: str = "accounts") -> str | None:
    """Dispatch to the account or content URL validator based on target type."""
    if target == "accounts":
        return _profile_filter(platform, url)
    return _content_filter(platform, url, target)


def _profile_filter(platform: str, url: str) -> str | None:
    """Return a canonical profile URL for the platform, or None if `url` is not one."""
    from urllib.parse import urlparse
    url = _canonical_social_url(url)
    try:
        p = urlparse(url)
    except Exception:
        return None
    host = (p.netloc or "").lower().replace("www.", "")
    path = (p.path or "").strip("/")
    if not path:
        return None

    if platform == "tiktok":
        if "tiktok.com" not in host:
            return None
        m = re.match(r"@([\w.\-]+)$", path)
        return f"https://www.tiktok.com/@{m.group(1)}" if m else None

    if platform == "instagram":
        if "instagram.com" not in host:
            return None
        m = re.match(r"([\w.\-]+)$", path)
        if not m or m.group(1).lower() in _IG_RESERVED:
            return None
        return f"https://www.instagram.com/{m.group(1)}/"

    if platform == "snapchat":
        if "snapchat.com" not in host:
            return None
        m = re.match(r"add/([\w.\-]+)$", path)
        return f"https://www.snapchat.com/add/{m.group(1)}" if m else None

    if platform == "youtube":
        if "youtube.com" not in host:
            return None
        if not re.match(r"(@[\w.\-]+|channel/[\w\-]+|c/[\w.\-]+|user/[\w.\-]+)$", path):
            return None
        if path.split("/")[0].lower() in _YT_RESERVED:
            return None
        return f"https://www.youtube.com/{path}"

    if platform == "twitter":
        if "twitter.com" not in host and "x.com" not in host:
            return None
        m = re.match(r"([A-Za-z0-9_]+)$", path)
        if not m or m.group(1).lower() in _TW_RESERVED:
            return None
        return f"https://twitter.com/{m.group(1)}"

    return None


def _direct_candidates(keyword: str, platform: str, target: str = "accounts") -> list[str]:
    """Generate exact URL/handle guesses before relying on search results."""
    from urllib.parse import urlparse

    if target != "accounts":
        candidate = _url_filter(platform, keyword, target)
        return [candidate] if candidate else []

    raw = (keyword or "").strip()
    if not raw:
        return []

    candidates: list[str] = []
    if re.search(r"(https?://|www\.|tiktok\.com|instagram\.com|snapchat\.com|youtube\.com|youtu\.be|twitter\.com|x\.com)", raw, re.I):
        normalized = _canonical_social_url(raw)
        direct = _profile_filter(platform, normalized)
        if direct:
            candidates.append(direct)

    tokens = re.findall(r"@([\w.\-]{2,})", raw)
    if not tokens and re.fullmatch(r"[\w.\-]{2,}", raw):
        tokens = [raw]
    for token in tokens[:4]:
        handle = token.strip(".-_")
        if not handle:
            continue
        guesses = {
            "tiktok": f"https://www.tiktok.com/@{handle}",
            "instagram": f"https://www.instagram.com/{handle}/",
            "snapchat": f"https://www.snapchat.com/add/{handle}",
            "youtube": f"https://www.youtube.com/@{handle}",
            "twitter": f"https://twitter.com/{handle}",
        }
        guess = guesses.get(platform)
        if guess:
            direct = _profile_filter(platform, guess)
            if direct:
                candidates.append(direct)

    # Keep order while deduping.
    seen, out = set(), []
    for url in candidates:
        key = urlparse(url).geturl().lower().rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def _search_query_variants(platform: str, keyword: str, location: str, target: str,
                           mentions: str = "") -> list[str]:
    base = _build_search_query(platform, keyword, location, target, mentions)
    variants = [base]

    # Mentions-specific query variants
    if mentions:
        m = mentions.lstrip("@")
        variants.extend([
            _build_search_query(platform, f'"{keyword}" @{m}', location, target, ""),
            _build_search_query(platform, f'"{keyword}" mentions @{m}', location, target, ""),
            _build_search_query(platform, f'{keyword} "{m}"', location, target, ""),
        ])

    if target == "accounts":
        label = {"tiktok": "TikTok", "instagram": "Instagram", "snapchat": "Snapchat",
                 "youtube": "YouTube", "twitter": "Twitter X"}.get(platform, platform)
        variants.extend([
            _build_search_query(platform, f'"{keyword}" {label} creator', location, target, mentions),
            _build_search_query(platform, f'"{keyword}" influencer profile', location, target, mentions),
            _build_search_query(platform, f'inurl:{keyword.replace(" ", "")}', location, target, mentions),
            f'"{keyword}" "{label}" profile {location}'.strip(),
            f'{keyword} {label} influencer {location}'.strip(),
            f'{keyword} "{label}" "followers" {location}'.strip(),
        ])
    elif target == "hashtags":
        variants.append(_build_search_query(platform, f'"#{keyword.lstrip("#")}"', location, target, mentions))
    elif target == "stories":
        variants.extend([
            _build_search_query(platform, f'"{keyword}" story', location, target, mentions),
            _build_search_query(platform, f'"{keyword}" stories', location, target, mentions),
        ])
    else:
        variants.append(_build_search_query(platform, f'"{keyword}"', location, target, mentions))

    out, seen = [], set()
    for q in variants:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


async def _ddg_search(query: str, *, timeout: int = 20, max_results: int = 60) -> list[str]:
    """Query DuckDuckGo's HTML endpoint and return de-duplicated result URLs."""
    from urllib.parse import parse_qs, unquote, urlparse
    out: list[str] = []
    resp = None
    for endpoint in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            resp = await fetch_http(
                endpoint,
                params={"q": query},
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/124.0 Safari/537.36"},
            )
            if getattr(resp, "status", 0) == 200:
                break
        except Exception:
            resp = None
    if resp is None:
        return out

    seen = set()
    for h in (resp.css("a::attr(href)").getall() or []):
        real = h
        if "uddg=" in h:
            try:
                href = h if h.startswith("http") else "https:" + h
                q = parse_qs(urlparse(href).query)
                real = unquote(q.get("uddg", [""])[0]) or h
            except Exception:
                real = h
        if not real.startswith("http") or real in seen:
            continue
        seen.add(real)
        out.append(real)
        if len(out) >= max_results:
            break
    return out


# ---------------------------------------------------------------------------
# API-based search providers
# ---------------------------------------------------------------------------

async def _search_querit(query: str, *, timeout: int = 20, max_results: int = 30) -> list[dict]:
    """Search via Querit API (https://querit.ai) using Scrapling."""
    if not _QUERIT_API_KEY:
        return []
    hits: list[dict] = []
    try:
        resp = await fetch_http(
            "https://api.querit.ai/v1/search",
            method="POST",
            timeout=timeout,
            headers={"Authorization": f"Bearer {_QUERIT_API_KEY}"},
            json_data={"query": query, "count": max_results},
        )
        body = _safe(getattr(resp, "body", resp))
        data = json.loads(body) if isinstance(body, str) else body
        if isinstance(data, dict):
            results = data.get("results", {})
            items = results.get("result", data.get("web", [])) if isinstance(results, dict) else results
            if isinstance(items, list):
                for item in items[:max_results]:
                    url = item.get("url", "") or item.get("link", "")
                    if url:
                        hits.append({"url": url, "title": item.get("title", ""), "snippet": item.get("snippet", "") or item.get("description", ""), "source": "querit"})
    except Exception:
        pass
    return hits


async def _search_tavily(query: str, *, timeout: int = 20, max_results: int = 30) -> list[dict]:
    """Search via Tavily API (https://tavily.com) using Scrapling."""
    if not _TAVILY_API_KEY:
        return []
    hits: list[dict] = []
    try:
        resp = await fetch_http(
            "https://api.tavily.com/search",
            method="POST",
            timeout=timeout,
            json_data={"api_key": _TAVILY_API_KEY, "query": query, "max_results": max_results, "search_depth": "advanced"},
        )
        body = _safe(getattr(resp, "body", resp))
        data = json.loads(body) if isinstance(body, str) else body
        for item in (data.get("results", []))[:max_results]:
            url = item.get("url", "")
            if url:
                hits.append({"url": url, "title": item.get("title", ""), "snippet": item.get("content", "") or item.get("snippet", ""), "source": "tavily"})
    except Exception:
        pass
    return hits


async def _search_serper(query: str, *, timeout: int = 20, max_results: int = 30) -> list[dict]:
    """Search via Serper.dev API (Google search) using Scrapling."""
    if not _SERPER_API_KEY:
        return []
    hits: list[dict] = []
    try:
        resp = await fetch_http(
            "https://google.serper.dev/search",
            method="POST",
            timeout=timeout,
            headers={"X-API-KEY": _SERPER_API_KEY},
            json_data={"q": query, "num": max_results},
        )
        body = _safe(getattr(resp, "body", resp))
        data = json.loads(body) if isinstance(body, str) else body
        for item in (data.get("organic", []))[:max_results]:
            url = item.get("link", "")
            if url:
                hits.append({"url": url, "title": item.get("title", ""), "snippet": item.get("snippet", ""), "source": "serper"})
    except Exception:
        pass
    return hits


async def _search_duckduckgo_api(query: str, *, timeout: int = 20, max_results: int = 30) -> list[dict]:
    """Search via DuckDuckGo instant-answer API using Scrapling."""
    hits: list[dict] = []
    try:
        resp = await fetch_http(
            "https://api.duckduckgo.com/",
            timeout=timeout,
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        body = _safe(getattr(resp, "body", resp))
        data = json.loads(body) if isinstance(body, str) else body
        if isinstance(data, dict):
            abstract_url = data.get("AbstractURL", "")
            if abstract_url:
                hits.append({"url": abstract_url, "title": data.get("Heading", ""), "snippet": data.get("AbstractText", ""), "source": "ddg_api"})
            for topic in data.get("RelatedTopics", [])[:max_results]:
                if "Topics" in topic:
                    for sub in topic["Topics"][:5]:
                        url = sub.get("FirstURL", "") or ""
                        if url:
                            hits.append({"url": url, "title": (sub.get("Text", "") or "")[:120], "snippet": "", "source": "ddg_api"})
                else:
                    url = topic.get("FirstURL", "") or ""
                    if url:
                        hits.append({"url": url, "title": (topic.get("Text", "") or "")[:120], "snippet": "", "source": "ddg_api"})
    except Exception:
        pass
    return hits


_ENABLED_PROVIDERS = {
    "bing": ("_search_engine_hits_via_bing", True),
    "duckduckgo": ("_search_engine_hits_via_ddg", True),
    "querit": ("_search_querit", bool(_QUERIT_API_KEY)),
    "tavily": ("_search_tavily", bool(_TAVILY_API_KEY)),
    "serper": ("_search_serper", bool(_SERPER_API_KEY)),
    "duckduckgo_api": ("_search_duckduckgo_api", True),
}


async def _search_aggregate(query: str, *, timeout: int = 20, max_results: int = 60,
                            providers: list[str] | None = None) -> list[dict]:
    """Search all enabled providers in parallel and aggregate deduplicated results."""

    provider_fns = {
        "bing": _search_bing,
        "duckduckgo": _search_ddg_html,
        "querit": _search_querit,
        "tavily": _search_tavily,
        "serper": _search_serper,
        "duckduckgo_api": _search_duckduckgo_api,
    }

    if providers is None:
        providers = [p for p, (_, enabled) in _ENABLED_PROVIDERS.items() if enabled]

    results = await asyncio.gather(
        *[provider_fns[p](query, timeout=timeout, max_results=max_results) for p in providers if p in provider_fns],
        return_exceptions=True,
    )

    seen: dict[str, dict] = {}
    aggregated: list[dict] = []
    for batch in results:
        if isinstance(batch, Exception) or not batch:
            continue
        for hit in batch:
            url = hit.get("url", "")
            key = url.lower().rstrip("/")
            if not key:
                continue
            source = str(hit.get("source", "search"))
            if key in seen:
                existing = seen[key]
                sources = existing.setdefault("sources", [existing.get("source", "search")])
                if source not in sources:
                    sources.append(source)
                if len(str(hit.get("snippet", ""))) > len(str(existing.get("snippet", ""))):
                    existing["snippet"] = hit.get("snippet", "")
                if len(str(hit.get("title", ""))) > len(str(existing.get("title", ""))):
                    existing["title"] = hit.get("title", "")
                continue
            normalized = {**hit, "sources": [source]}
            seen[key] = normalized
            aggregated.append(normalized)

    return aggregated[:max_results]


def _parse_bing_rss(body: str, *, max_results: int = 30) -> list[dict]:
    """Parse Bing's public RSS result format into the shared search-hit schema."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(body)
    except (ET.ParseError, TypeError):
        return []
    hits: list[dict] = []
    seen: set[str] = set()
    for item in root.findall(".//item"):
        url = (item.findtext("link") or "").strip()
        key = url.casefold().rstrip("/")
        if not url.startswith("http") or key in seen:
            continue
        seen.add(key)
        description = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
        published = (item.findtext("pubDate") or "").strip()
        hits.append({
            "url": url,
            "title": _safe(item.findtext("title") or ""),
            "snippet": re.sub(r"\s+", " ", description).strip(),
            "published_at": published,
            "date_precision": "approximate" if published else "unknown",
            "source": "bing_rss",
        })
        if len(hits) >= max_results:
            break
    return hits


async def _search_bing(query: str, *, timeout: int = 20, max_results: int = 30) -> list[dict]:
    """Bing HTML scraping search (original implementation)."""
    from urllib.parse import parse_qs, urlparse

    def unwrap(href: str) -> str:
        if not href:
            return ""
        if "bing.com/ck/" in href:
            try:
                q = parse_qs(urlparse(href).query)
                encoded = q.get("u", [""])[0]
                if encoded.startswith("a1"):
                    payload = encoded[2:]
                    payload += "=" * (-len(payload) % 4)
                    return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8", "replace")
            except Exception:
                return href
        return href

    hits: list[dict] = []
    seen: set[str] = set()

    def add(url: str, title: str = "", snippet: str = "", source: str = "bing"):
        url = unwrap(url)
        if not url.startswith("http"):
            return
        key = url.lower().rstrip("/")
        if key in seen:
            return
        seen.add(key)
        hits.append({"url": url, "title": _safe(title), "snippet": _safe(snippet), "source": source})

    try:
        resp = await fetch_http(
            "https://www.bing.com/search",
            params={"q": query, "count": min(max(max_results, 10), 50)},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
        )
        for node in resp.css("li.b_algo"):
            href = node.css("h2 a::attr(href)").get() or node.css("a::attr(href)").get() or ""
            title = node.css("h2 a::text").get() or node.css("a::text").get() or ""
            snippet = " ".join(node.css(".b_caption p::text, p::text").getall() or [])
            add(href, title, snippet, "bing")
            if len(hits) >= max_results:
                break
    except Exception:
        pass
    if not hits:
        try:
            rss_response = await fetch_http(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss", "count": min(max(max_results, 10), 50)},
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            hits.extend(_parse_bing_rss(
                _safe(getattr(rss_response, "body", "") or ""),
                max_results=max_results,
            ))
        except Exception:
            pass
    return hits[:max_results]


async def _search_ddg_html(query: str, *, timeout: int = 20, max_results: int = 30) -> list[dict]:
    """DuckDuckGo HTML scrape search (original fallback)."""
    from urllib.parse import parse_qs, unquote, urlparse

    def unwrap(href: str) -> str:
        if not href:
            return ""
        if "uddg=" in href:
            try:
                wrapped = href if href.startswith("http") else "https:" + href
                return unquote(parse_qs(urlparse(wrapped).query).get("uddg", [""])[0]) or href
            except Exception:
                return href
        return href

    hits: list[dict] = []
    seen: set[str] = set()

    def add(url: str, title: str = "", snippet: str = "", source: str = "duckduckgo"):
        url = unwrap(url)
        if not url.startswith("http"):
            return
        key = url.lower().rstrip("/")
        if key in seen:
            return
        seen.add(key)
        hits.append({"url": url, "title": _safe(title), "snippet": _safe(snippet), "source": source})

    for endpoint in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            resp = await fetch_http(
                endpoint,
                params={"q": query},
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
            )
            for node in resp.css(".result, tr"):
                href = node.css("a::attr(href)").get() or ""
                title = " ".join(node.css("a::text").getall() or [])
                snippet = " ".join(node.css(".result__snippet::text, td::text").getall() or [])
                add(href, title, snippet, "duckduckgo")
                if len(hits) >= max_results:
                    break
            if hits:
                break
        except Exception:
            continue
    return hits[:max_results]


async def _search_engine_hits(query: str, *, timeout: int = 20, max_results: int = 60) -> list[dict]:
    """Aggregate search results from all enabled providers (API + scrape)."""
    return await _search_aggregate(query, timeout=timeout, max_results=max_results)


# ---------------------------------------------------------------------------
# Post Finder: find a user's posts/videos/reels matching a hashtag or @mention
# Returns post URLs only — no content scraping.
# ---------------------------------------------------------------------------

_POST_PLATFORMS = ("tiktok", "instagram", "youtube", "twitter", "snapchat")


def _parse_terms(raw_terms) -> list[dict]:
    """Normalize hashtag/mention terms.

    '#tag' -> hashtag, '@user' -> mention, bare word -> matches either.
    Accepts a list or a comma/newline-separated string.
    """
    if isinstance(raw_terms, str):
        raw_terms = re.split(r"[,\n]+", raw_terms)
    terms: list[dict] = []
    seen: set[tuple] = set()
    for t in raw_terms or []:
        t = str(t).strip()
        if not t:
            continue
        kind = "any"
        if t.startswith("#"):
            kind = "hashtag"
        elif t.startswith("@"):
            kind = "mention"
        word = t.lstrip("#@").strip()
        if word and (kind, word.lower()) not in seen:
            seen.add((kind, word.lower()))
            terms.append({"kind": kind, "word": word})
    return terms


def _term_label(term: dict) -> str:
    prefix = {"hashtag": "#", "mention": "@"}.get(term["kind"], "")
    return prefix + term["word"]


def _term_hit(text: str, term: dict) -> bool:
    """Check whether `text` contains the hashtag/mention/word."""
    word = re.escape(term["word"])
    if term["kind"] == "hashtag":
        pat = rf"#{word}\b"
    elif term["kind"] == "mention":
        pat = rf"@{word}\b"
    else:
        pat = rf"(?<![\w#@])[#@]?{word}\b"
    return bool(re.search(pat, text or "", re.I))


def _match_terms(terms: list[dict], caption: str, hashtags=(), mentions=()) -> list[str]:
    """Return labels of terms matched by a post's caption / tag / mention lists."""
    tags_lc = {str(h).lower() for h in hashtags}
    mentions_lc = {str(m).lower() for m in mentions}
    matched = []
    for t in terms:
        w = t["word"].lower()
        ok = (
            (t["kind"] in ("hashtag", "any") and w in tags_lc)
            or (t["kind"] in ("mention", "any") and w in mentions_lc)
            or _term_hit(caption, t)
        )
        if ok:
            matched.append(_term_label(t))
    return matched


def _post_owner(platform: str, url: str) -> str:
    """Extract the author's username from a post URL, when the platform embeds it."""
    if platform == "tiktok":
        m = re.search(r"tiktok\.com/@([\w.\-]+)/video/", url)
        return m.group(1) if m else ""
    if platform == "twitter":
        m = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)/status/", url)
        return m.group(1) if m else ""
    return ""


def _canon_post_url(platform: str, url: str) -> str | None:
    """Canonicalize any post/video/reel URL for the platform (None if not one)."""
    for target in ("videos", "posts"):
        cu = _content_filter(platform, url, target)
        if cu:
            return cu
    return None


def _user_posts_queries(platform: str, username: str, term: dict) -> list[str]:
    """Build search-engine queries scoped to a user's post URLs + the term."""
    u = username.lstrip("@")
    tokens = []
    if term["kind"] in ("hashtag", "any"):
        tokens.append(f'"#{term["word"]}"')
    if term["kind"] in ("mention", "any"):
        tokens.append(f'"@{term["word"]}"')
    queries: list[str] = []
    for tok in tokens:
        # Loose word for engines that return nothing on strict site:+quotes
        # dorks (results are still filtered by canonical post URL + owner).
        loose = tok.strip('"')
        if platform == "tiktok":
            queries += [
                f"site:tiktok.com/@{u}/video {tok}",
                f'site:tiktok.com "@{u}" {tok}',
                f"{u} tiktok video {loose}",
            ]
        elif platform == "instagram":
            queries += [
                f'site:instagram.com/reel "{u}" {tok}',
                f'site:instagram.com/p "{u}" {tok}',
                f'site:instagram.com "{u} on instagram" {tok}',
                f"{u} instagram reel {loose}",
            ]
        elif platform == "youtube":
            queries += [
                f'site:youtube.com/watch "{u}" {tok}',
                f'site:youtube.com/shorts "{u}" {tok}',
                f"{u} youtube {loose}",
            ]
        elif platform == "twitter":
            queries += [
                f"site:x.com/{u}/status {tok}",
                f"site:twitter.com/{u}/status {tok}",
                f"{u} twitter {loose}",
            ]
    return queries


def _brand_posts_queries(platform: str, term: dict) -> list[str]:
    """Build public-web queries for posts mentioning or hashtagging a brand."""
    label = _term_label(term)
    quoted = f'"{label}"'
    loose = label.lstrip("#@")
    if platform == "tiktok":
        return [
            f"site:tiktok.com inurl:/video/ {quoted}",
            f"site:tiktok.com/@ {quoted}",
            f"tiktok video {quoted}",
            f"tiktok {loose}",
        ]
    if platform == "snapchat":
        return [
            f"site:snapchat.com/spotlight {quoted}",
            f"site:snapchat.com/add {quoted}",
            f"snapchat spotlight {quoted}",
        ]
    if platform == "instagram":
        return [
            f"site:instagram.com/reel {quoted}",
            f"site:instagram.com/p {quoted}",
            f"site:instagram.com {quoted}",
            f"instagram reel {quoted}",
        ]
    if platform == "youtube":
        return [
            f"site:youtube.com/watch {quoted}",
            f"site:youtube.com/shorts {quoted}",
            f"youtube {quoted}",
        ]
    if platform == "twitter":
        return [
            f"site:x.com inurl:/status/ {quoted}",
            f"site:twitter.com inurl:/status/ {quoted}",
            f"twitter {quoted}",
        ]
    return []


def _post_author_from_hit(platform: str, url: str, title: str = "",
                          snippet: str = "") -> str:
    """Extract a public post author from its URL or search-result metadata."""
    owner = _post_owner(platform, url)
    if owner:
        return owner
    if platform == "snapchat":
        match = re.search(r"snapchat\.com/(?:add|spotlight)/([A-Za-z0-9._-]{2,})", url, re.I)
        if match:
            return match.group(1)
    text = html.unescape(f"{title} {snippet}")
    patterns = (
        r"\(@([A-Za-z0-9._-]{2,})\)",
        r"(?:Instagram|YouTube)\s*[·|:-]\s*@?([A-Za-z0-9._-]{2,})",
        r"(?:from|by)\s+[^(@]{0,80}\(@([A-Za-z0-9._-]{2,})\)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return ""


async def _post_author_from_page(platform: str, url: str, *, timeout: int = 20,
                                 proxy: str = "") -> str:
    """Resolve an author from a public post page when its URL omits the handle."""
    if platform not in {"instagram", "youtube"}:
        return _post_owner(platform, url)
    try:
        response = await fetch_http(url, timeout=timeout, proxy=proxy, retries=1)
        raw = html.unescape(_safe(getattr(response, "body", "") or ""))
    except Exception:
        return ""
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'][^"\']*'
        r'\(@([A-Za-z0-9._-]{2,})\)',
        r'"owner"\s*:\s*\{[^{}]{0,500}"username"\s*:\s*"([A-Za-z0-9._-]{2,})"',
        r'"author"\s*:\s*\{[^{}]{0,500}"uniqueId"\s*:\s*"([A-Za-z0-9._-]{2,})"',
    ):
        match = re.search(pattern, raw, re.I)
        if match:
            return match.group(1)
    return ""


def _collect_tiktok_items(node, items: dict[str, dict]):
    """Recursively collect TikTok video items (id + desc) from any JSON tree."""
    if isinstance(node, dict):
        vid = node.get("id")
        if isinstance(vid, str) and vid.isdigit() and len(vid) >= 15 and "desc" in node:
            author = node.get("author")
            if isinstance(author, dict):
                author = author.get("uniqueId", "")
            elif not isinstance(author, str):
                author = ""
            desc = str(node.get("desc") or "")
            tags = [str(t).lower() for t in re.findall(r"#(\w+)", desc)]
            mentions = [str(m).lower() for m in re.findall(r"@([\w.\-]+)", desc)]
            for te in node.get("textExtra") or []:
                if isinstance(te, dict):
                    if te.get("hashtagName"):
                        tags.append(str(te["hashtagName"]).lower())
                    if te.get("userUniqueId"):
                        mentions.append(str(te["userUniqueId"]).lower())
            items.setdefault(vid, {
                "id": vid, "desc": desc, "author": author,
                "hashtags": tags, "mentions": mentions,
            })
        for v in node.values():
            _collect_tiktok_items(v, items)
    elif isinstance(node, list):
        for v in node:
            _collect_tiktok_items(v, items)


def _tiktok_items_from_html(raw: str) -> dict[str, dict]:
    """Extract video items from the JSON blobs TikTok embeds in profile HTML."""
    items: dict[str, dict] = {}
    for pat in (
        r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>',
    ):
        m = re.search(pat, raw, re.DOTALL)
        if not m:
            continue
        try:
            _collect_tiktok_items(json.loads(m.group(1)), items)
        except Exception:
            continue
    return items


async def _tiktok_profile_videos(username: str, *, timeout: int = 20, proxy: str = "",
                                 mode: str = "http") -> list[dict]:
    """Fetch a public TikTok profile and return its embedded recent video items."""
    url = f"https://www.tiktok.com/@{username}"
    items: dict[str, dict] = {}
    try:
        resp = await fetch_http(url, timeout=timeout, proxy=proxy)
        items = _tiktok_items_from_html(_safe(getattr(resp, "body", "") or ""))
    except Exception:
        items = {}
    # TikTok serves plain-HTTP clients a bot wall with no embedded data;
    # auto-escalate through Scrapling's browser fetchers when available.
    # Bundled Chromium can fail to launch on some machines, so retry with
    # the locally installed Chrome (DynamicFetcher real_chrome).
    if not items and _HAS_PLAYWRIGHT:
        attempts = ([("stealth", {})] if mode == "stealth" else []) + [
            ("browser", {}),
            ("browser", {"real_chrome": True}),
        ]
        for kind, extra in attempts:
            try:
                fetch_fn = fetch_stealth if kind == "stealth" else fetch_browser
                # No wait_selector here: the grid can show "Something went wrong"
                # while the item_list XHR still delivers the videos.
                resp = await fetch_fn(url, timeout=max(timeout, 30) * 1000, proxy=proxy,
                                      network_idle=True,
                                      capture_xhr=r"api/post/item_list", **extra)
                items = _tiktok_items_from_html(_safe(getattr(resp, "body", "") or ""))
                # The video list usually arrives via a captured XHR, not the HTML.
                for xhr in getattr(resp, "captured_xhr", None) or []:
                    try:
                        _collect_tiktok_items(
                            json.loads(_safe(getattr(xhr, "body", "") or "")), items)
                    except Exception:
                        continue
                if not items:
                    items = _tiktok_items_from_dom(resp, username)
                if items:
                    break
            except Exception:
                continue
    return list(items.values())


def _tiktok_items_from_dom(resp, username: str) -> dict[str, dict]:
    """Fallback: read video tiles from rendered profile DOM.

    The thumbnail's alt text carries the caption, which is enough to match
    hashtags/mentions when the embedded JSON no longer lists the videos.
    """
    items: dict[str, dict] = {}
    try:
        anchors = resp.css('a[href*="/video/"]')
    except Exception:
        return items
    for a in anchors or []:
        try:
            href = a.attrib.get("href", "")
            m = re.search(r"@([\w.\-]+)/video/(\d+)", href)
            if not m or m.group(1).lower() != username.lower():
                continue
            vid = m.group(2)
            desc = _safe(a.css("img::attr(alt)").get() or a.attrib.get("title") or "")
            desc = re.sub(r"\s*\|\s*TikTok\s*$", "", desc)
            items.setdefault(vid, {
                "id": vid, "desc": desc, "author": username,
                "hashtags": [t.lower() for t in re.findall(r"#(\w+)", desc)],
                "mentions": [u.lower() for u in re.findall(r"@([\w.\-]+)", desc)],
            })
        except Exception:
            continue
    return items


async def _tiktok_public_search_posts(terms: list[dict], *, timeout: int = 30,
                                      proxy: str = "", mode: str = "browser",
                                      max_results: int = 100) -> list[dict]:
    """Use TikTok's public rendered search to discover videos for brand terms."""
    from urllib.parse import quote

    unique_terms: list[dict] = []
    seen_words: set[str] = set()
    for term in terms:
        word = term["word"].lower()
        if word not in seen_words:
            seen_words.add(word)
            unique_terms.append(term)
    out: dict[str, dict] = {}
    for term in unique_terms[:8]:
        # TikTok's anonymous search currently returns an empty item feed for
        # prefixed @/# queries, but its raw keyword preview still contains the
        # public result cards. Matching below remains strict on mention/tag.
        query = term["word"]
        url = f"https://www.tiktok.com/search/video?q={quote(query)}"
        response = None
        try:
            if mode in {"browser", "stealth"} and _HAS_PLAYWRIGHT:
                fetch_fn = fetch_stealth if mode == "stealth" else fetch_browser
                response = await fetch_fn(
                    url,
                    timeout=max(timeout, 30) * 1000,
                    proxy=proxy,
                    network_idle=True,
                    wait=1800,
                    capture_xhr=r"api/search",
                )
            else:
                response = await fetch_http(url, timeout=timeout, proxy=proxy)
        except Exception:
            response = None
        if response is None:
            continue

        items = _tiktok_items_from_html(
            _safe(getattr(response, "body", "") or "")
        )
        for xhr in getattr(response, "captured_xhr", None) or []:
            try:
                _collect_tiktok_items(
                    json.loads(_safe(getattr(xhr, "body", "") or "")), items
                )
            except Exception:
                continue
        for item in items.values():
            author = str(item.get("author") or "").strip()
            video_id = str(item.get("id") or "").strip()
            if not author or not video_id:
                continue
            matched = _match_terms(
                terms,
                item.get("desc", ""),
                item.get("hashtags", []),
                item.get("mentions", []),
            )
            if not matched:
                continue
            post_url = f"https://www.tiktok.com/@{author}/video/{video_id}"
            existing = out.get(post_url)
            if existing:
                for label in matched:
                    if label not in existing["matched_terms"]:
                        existing["matched_terms"].append(label)
                continue
            out[post_url] = {
                "url": post_url,
                "platform": "tiktok",
                "author_username": author,
                "author_url": f"https://www.tiktok.com/@{author}",
                "matched_terms": matched,
                "source": "tiktok_public_search",
                "search_sources": ["tiktok_public_search"],
                "title": "",
                "snippet": _safe(item.get("desc", ""))[:260],
                "caption": _safe(item.get("desc", ""))[:2000],
                "hashtags": item.get("hashtags", []),
                "mentions": item.get("mentions", []),
                "verification_method": "native_structured",
                "confidence": "high",
                "target": "posts",
                "status": 200,
            }
            if len(out) >= max_results:
                return list(out.values())
    return list(out.values())


def _youtube_text(node: object) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return str(node["simpleText"])
    return "".join(
        str(run.get("text", ""))
        for run in node.get("runs", [])
        if isinstance(run, dict)
    )


def _youtube_items_from_search_html(raw: str) -> list[dict]:
    """Extract public video and creator metadata from YouTube search HTML."""
    match = re.search(r"var ytInitialData = (\{.*?\});</script>", raw, re.DOTALL)
    if not match:
        match = re.search(r"ytInitialData\s*=\s*(\{.*?\});", raw, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except (TypeError, json.JSONDecodeError):
        return []

    renderers: list[dict] = []

    def collect(node: object) -> None:
        if isinstance(node, dict):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict):
                renderers.append(renderer)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(payload)
    items: list[dict] = []
    seen: set[str] = set()
    for renderer in renderers:
        video_id = str(renderer.get("videoId") or "").strip()
        if not video_id or video_id in seen:
            continue
        owner = renderer.get("ownerText") or renderer.get("longBylineText") or {}
        owner_runs = owner.get("runs", []) if isinstance(owner, dict) else []
        owner_run = owner_runs[0] if owner_runs and isinstance(owner_runs[0], dict) else {}
        browse = (owner_run.get("navigationEndpoint") or {}).get("browseEndpoint") or {}
        owner_path = str(browse.get("canonicalBaseUrl") or "").strip()
        author = owner_path.removeprefix("/@").strip("/").casefold()
        if not author or not re.fullmatch(r"[\w.\-]+", author):
            continue
        seen.add(video_id)
        items.append({
            "video_id": video_id,
            "title": _youtube_text(renderer.get("title")),
            "description": _youtube_text(renderer.get("descriptionSnippet")),
            "author": author,
            "author_name": _youtube_text(owner),
            "author_url": f"https://www.youtube.com/@{author}",
            "published_text": _youtube_text(renderer.get("publishedTimeText")),
        })
    return items


def _relative_time_iso(text: str) -> str:
    """Convert YouTube's English relative timestamp to an approximate UTC instant."""
    from datetime import datetime, timedelta, timezone

    match = re.search(
        r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", text or "", re.I
    )
    if not match:
        return ""
    value = int(match.group(1))
    unit = match.group(2).lower()
    days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(unit, 0) * value
    delta = timedelta(days=days)
    if unit == "hour":
        delta = timedelta(hours=value)
    elif unit == "minute":
        delta = timedelta(minutes=value)
    return (datetime.now(timezone.utc) - delta).isoformat().replace("+00:00", "Z")


async def _youtube_public_search_posts(
    terms: list[dict], *, timeout: int = 20, max_results: int = 100
) -> list[dict]:
    """Discover real public YouTube videos from first-party search metadata."""
    unique_words = list(dict.fromkeys(
        str(term.get("word") or "").casefold() for term in terms if term.get("word")
    ))
    out: dict[str, dict] = {}
    for word in unique_words[:8]:
        try:
            response = await fetch_http(
                "https://www.youtube.com/results",
                params={"search_query": word},
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 Chrome/124 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        except Exception:
            continue
        for item in _youtube_items_from_search_html(
            _safe(getattr(response, "body", "") or "")
        ):
            text = f'{item["title"]} {item["description"]}'
            matched = _match_terms(terms, text)
            if not matched:
                continue
            url = f'https://www.youtube.com/watch?v={item["video_id"]}'
            existing = out.get(url)
            if existing:
                existing["matched_terms"] = list(dict.fromkeys([
                    *existing["matched_terms"], *matched,
                ]))
                continue
            out[url] = {
                "url": url,
                "platform": "youtube",
                "author_username": item["author"],
                "author_url": item["author_url"],
                "author_name": item["author_name"],
                "matched_terms": matched,
                "source": "youtube_public_search",
                "search_sources": ["youtube_public_search"],
                "title": item["title"],
                "snippet": item["description"],
                "caption": text,
                "published_at": _relative_time_iso(item["published_text"]),
                "published_text": item["published_text"],
                "date_precision": "approximate" if item["published_text"] else "unknown",
                "verification_method": "native_structured",
                "target": "posts",
                "status": 200,
            }
            if len(out) >= max_results:
                return list(out.values())
    return list(out.values())


# Instagram's anonymous web API blocks bursts (~20 quick requests -> 401
# "wait a few minutes"). All calls go through a shared pacing gate, and a
# rate-limit response triggers a global cooldown + retry so bulk runs recover.
_IG_MIN_INTERVAL = float(os.environ.get("IG_MIN_INTERVAL", "2.5"))
_IG_COOLDOWN = float(os.environ.get("IG_COOLDOWN", "75"))
_IG_NEXT_AT = [0.0]
_IG_LOCKS: dict[int, Any] = {}


def _ig_gate_lock():
    loop = asyncio.get_event_loop()
    lock = _IG_LOCKS.get(id(loop))
    if lock is None:
        lock = asyncio.Lock()
        _IG_LOCKS.clear()  # locks from dead loops are unusable anyway
        _IG_LOCKS[id(loop)] = lock
    return lock


async def _instagram_profile_posts(username: str, *, timeout: int = 20, proxy: str = "",
                                   retries: int = 2) -> list[dict]:
    """Fetch recent posts of a public Instagram profile via the anonymous web API."""
    out: list[dict] = []
    payload = None
    for _attempt in range(retries + 1):
        async with _ig_gate_lock():
            delay = _IG_NEXT_AT[0] - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            _IG_NEXT_AT[0] = time.time() + _IG_MIN_INTERVAL
        try:
            resp = await fetch_http(
                f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
                timeout=timeout,
                proxy=proxy,
                retries=1,
                headers={
                    "x-ig-app-id": "936619743392459",
                    "Accept": "*/*",
                    "Referer": f"https://www.instagram.com/{username}/",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            status = getattr(resp, "status", 0)
            raw = _safe(getattr(resp, "body", "") or "")
            if status in (401, 429) or "wait a few minutes" in raw[:200].lower():
                # Rate-limited: push the global gate out and retry after cooldown.
                _IG_NEXT_AT[0] = max(_IG_NEXT_AT[0], time.time() + _IG_COOLDOWN)
                continue
            if status != 200:
                return out
            payload = json.loads(raw or "{}")
            break
        except Exception:
            continue
    if payload is None:
        return out
    user = (payload.get("data") or {}).get("user") or {}
    edges = (user.get("edge_owner_to_timeline_media") or {}).get("edges") or []
    for edge in edges:
        node = edge.get("node") or {}
        code = node.get("shortcode")
        if not code:
            continue
        caption = ""
        cap_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        if cap_edges:
            caption = str((cap_edges[0].get("node") or {}).get("text") or "")
        is_reel = node.get("product_type") == "clips" or (
            node.get("is_video") and node.get("__typename") == "GraphVideo")
        out.append({
            "shortcode": code,
            "url": f"https://www.instagram.com/{'reel' if is_reel else 'p'}/{code}/",
            "caption": caption,
            "hashtags": [t.lower() for t in re.findall(r"#(\w+)", caption)],
            "mentions": [m.lower() for m in re.findall(r"@([\w.\-]+)", caption)],
        })
    return out


async def _youtube_channel_search(username: str, query: str, *, timeout: int = 20,
                                  proxy: str = "", max_results: int = 60) -> list[str]:
    """Search inside a YouTube channel (public page) and return matching video ids."""
    from urllib.parse import quote
    handle = username if username.startswith("@") else f"@{username}"
    out: list[str] = []
    seen: set[str] = set()
    try:
        resp = await fetch_http(
            f"https://www.youtube.com/{handle}/search?query={quote(query)}",
            timeout=timeout,
            proxy=proxy,
            headers={"Accept-Language": "en-US,en;q=0.9"},
            cookies={"CONSENT": "YES+1", "SOCS": "CAI"},
        )
        raw = _safe(getattr(resp, "body", "") or "")
    except Exception:
        return out
    for m in re.finditer(r'"videoId"\s*:\s*"([\w\-]{11})"', raw):
        vid = m.group(1)
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
        if len(out) >= max_results:
            break
    return out


async def _find_user_posts(username: str, terms: list[dict], platforms: list[str], *,
                           per_platform: int = 25, timeout: int = 20, deep: bool = True,
                           engines: bool = True, mode: str = "http", proxy: str = "",
                           search_providers: list[str] | None = None) -> dict[str, list[dict]]:
    """Find a user's public posts/videos/reels containing the given terms.

    Combines a deep scan of the user's public profile data (high confidence)
    with search-engine queries scoped to the user's post URLs. Only URLs and
    light metadata are returned — post content is never stored.
    """
    uname = str(username or "").strip()
    if "://" in uname or uname.lower().startswith("www."):
        uname = _username_from_url(_canonical_social_url(uname))
    uname = re.sub(r"[^\w.\-]", "", uname.lstrip("@"))
    if not uname or not terms:
        return {p: [] for p in platforms}

    async def _one(platform: str):
        found: list[dict] = []
        seen: set[str] = set()

        def add(url: str, *, matched: list[str], source: str, title: str = "",
                snippet: str = "", confidence: str = "high", post_id: str = ""):
            key = url.lower().rstrip("/")
            if key in seen or len(found) >= per_platform:
                return
            seen.add(key)
            profile_urls = _direct_candidates(uname, platform, "accounts")
            found.append({
                "url": url,
                "platform": platform,
                "username": uname,
                "author_username": uname,
                "author_url": profile_urls[0] if profile_urls else "",
                "post_id": post_id,
                "matched_terms": matched,
                "source": source,
                "title": _safe(title)[:160],
                "snippet": _safe(snippet)[:200],
                "confidence": confidence,
                "target": "posts",
                "status": 200,
            })

        # Phase 1: deep scan of the user's own public profile data (high confidence).
        if deep:
            try:
                if platform == "tiktok":
                    for it in await _tiktok_profile_videos(uname, timeout=timeout, proxy=proxy, mode=mode):
                        if it["author"] and it["author"].lower() != uname.lower():
                            continue
                        matched = _match_terms(terms, it["desc"], it["hashtags"], it["mentions"])
                        if matched:
                            add(f"https://www.tiktok.com/@{uname}/video/{it['id']}",
                                matched=matched, source="profile_scan",
                                snippet=it["desc"], post_id=it["id"])
                elif platform == "instagram":
                    for it in await _instagram_profile_posts(uname, timeout=timeout, proxy=proxy):
                        matched = _match_terms(terms, it["caption"], it["hashtags"], it["mentions"])
                        if matched:
                            add(it["url"], matched=matched, source="profile_scan",
                                snippet=it["caption"], post_id=it["shortcode"])
                elif platform == "youtube":
                    for term in terms:
                        prefixes = {"hashtag": ["#"], "mention": ["@"]}.get(term["kind"], ["#", "@"])
                        for prefix in prefixes:
                            for vid in await _youtube_channel_search(
                                    uname, prefix + term["word"], timeout=timeout, proxy=proxy):
                                add(f"https://www.youtube.com/watch?v={vid}",
                                    matched=[_term_label(term)], source="channel_search",
                                    post_id=vid)
            except Exception:
                pass

        # Phase 2: search engines, scoped to the user's post URLs (fills the rest,
        # and reaches older posts the profile page no longer embeds).
        if engines and len(found) < per_platform:
            jobs = [(term, q) for term in terms
                    for q in _user_posts_queries(platform, uname, term)]
            batches = await asyncio.gather(
                *[_search_aggregate(q, timeout=timeout, max_results=per_platform * 2,
                                    providers=search_providers) for _, q in jobs],
                return_exceptions=True,
            )
            for (term, _q), hits in zip(jobs, batches, strict=True):
                if isinstance(hits, Exception) or not hits:
                    continue
                for hit in hits:
                    cu = _canon_post_url(platform, hit.get("url", ""))
                    if not cu:
                        continue
                    text = f'{hit.get("title", "")} {hit.get("snippet", "")}'
                    owner = _post_owner(platform, cu)
                    if owner:
                        # URL itself proves authorship (tiktok/twitter).
                        if owner.lower() != uname.lower():
                            continue
                    elif uname.lower() not in f"{cu} {text}".lower():
                        # No owner in URL: require the handle in title/snippet.
                        continue
                    add(cu, matched=[_term_label(term)],
                        source=f'search:{hit.get("source", "web")}',
                        title=hit.get("title", ""), snippet=hit.get("snippet", ""),
                        confidence="high" if _term_hit(text, term) else "medium")
                if len(found) >= per_platform:
                    break
        return platform, found

    pairs = await asyncio.gather(*[_one(p) for p in platforms], return_exceptions=True)
    out: dict[str, list[dict]] = {}
    for pair in pairs:
        if isinstance(pair, Exception):
            continue
        platform, found = pair
        out[platform] = found
    return out


async def _find_brand_mentions(terms: list[dict], platforms: list[str], *,
                               per_platform: int = 50, timeout: int = 20,
                               proxy: str = "", mode: str = "http",
                               search_providers: list[str] | None = None,
                               excluded_handles: set[str] | None = None,
                               enrich_creators: bool = True,
                               concurrency: int = 4,
                               recency: str = "any",
                               aliases: list[str] | None = None) -> dict:
    """Discover public posts mentioning a brand and return their creator profiles.

    Search engines provide the broad discovery layer. Results are restricted to
    canonical public post URLs, authors are resolved from the URL/metadata/page,
    and creator profiles are enriched through the same scraper pipeline.
    """
    excluded = {str(h).lower().lstrip("@") for h in (excluded_handles or set())}
    recency = normalize_recency(recency)
    aliases = aliases or expand_brand_aliases([], [_term_label(term) for term in terms])
    rows: list[dict] = []
    seen_posts: set[str] = set()
    warnings: list[dict] = []
    coverage = {
        platform: {
            "platform": platform, "status": "pending", "sources_attempted": [],
            "sources_succeeded": [], "candidates": 0, "evidence": 0,
            "verified": 0, "warnings": [],
        }
        for platform in platforms
    }

    async def _platform_posts(platform: str) -> list[dict]:
        candidates: list[dict] = []
        local_seen: set[str] = set()
        if platform == "tiktok":
            coverage[platform]["sources_attempted"].append("native_public_search")
            native_rows = await _tiktok_public_search_posts(
                terms,
                timeout=timeout,
                proxy=proxy,
                mode=mode,
                max_results=per_platform,
            )
            for row in native_rows:
                key = row["url"].lower().rstrip("/")
                if key not in local_seen:
                    local_seen.add(key)
                    row["_key"] = key
                    candidates.append(row)
            if native_rows:
                coverage[platform]["sources_succeeded"].append("native_public_search")
        elif platform == "youtube":
            coverage[platform]["sources_attempted"].append("native_public_search")
            native_rows = await _youtube_public_search_posts(
                terms,
                timeout=timeout,
                max_results=max(per_platform * 4, 30),
            )
            for row in native_rows:
                key = row["url"].lower().rstrip("/")
                if key not in local_seen:
                    local_seen.add(key)
                    row["_key"] = key
                    candidates.append(row)
            if native_rows:
                coverage[platform]["sources_succeeded"].append("native_public_search")

        jobs = [
            (term, query)
            for term in terms
            for query in _brand_posts_queries(platform, term)
        ]
        if not jobs:
            return candidates
        coverage[platform]["sources_attempted"].append("public_search")
        batches = await asyncio.gather(
            *[
                _search_aggregate(
                    query,
                    timeout=timeout,
                    max_results=max(per_platform * 2, 30),
                    providers=search_providers,
                )
                for _, query in jobs
            ],
            return_exceptions=True,
        )
        if batches and all(isinstance(hits, Exception) for hits in batches):
            raise RuntimeError("All public search queries failed")
        for (term, _query), hits in zip(jobs, batches, strict=True):
            if isinstance(hits, Exception) or not hits:
                continue
            for hit in hits:
                post_url = _canon_post_url(platform, hit.get("url", ""))
                if not post_url:
                    continue
                key = post_url.lower().rstrip("/")
                if key in local_seen:
                    existing = next((r for r in candidates if r["_key"] == key), None)
                    if existing and _term_label(term) not in existing["matched_terms"]:
                        existing["matched_terms"].append(_term_label(term))
                    continue
                text = f'{hit.get("title", "")} {hit.get("snippet", "")}'
                owner = _post_author_from_hit(
                    platform, post_url, hit.get("title", ""), hit.get("snippet", "")
                )
                local_seen.add(key)
                candidates.append({
                    "_key": key,
                    "url": post_url,
                    "platform": platform,
                    "author_username": owner,
                    "matched_terms": [_term_label(term)],
                    "source": f'search:{hit.get("source", "web")}',
                    "search_sources": hit.get("sources") or [hit.get("source", "web")],
                    "title": _safe(hit.get("title", ""))[:180],
                    "snippet": _safe(hit.get("snippet", ""))[:260],
                    "confidence": "high" if _term_hit(text, term) else "medium",
                    "target": "posts",
                    "status": 200,
                })
                if len(candidates) >= per_platform:
                    break
            if len(candidates) >= per_platform:
                break
        if any(not isinstance(hits, Exception) and hits for hits in batches):
            coverage[platform]["sources_succeeded"].append("public_search")

        unresolved = [row for row in candidates if not row["author_username"]]
        sem = asyncio.Semaphore(max(1, min(concurrency, 6)))

        async def _resolve(row: dict):
            async with sem:
                row["author_username"] = await _post_author_from_page(
                    platform, row["url"], timeout=timeout, proxy=proxy
                )

        await asyncio.gather(*[_resolve(row) for row in unresolved], return_exceptions=True)
        clean: list[dict] = []
        for row in candidates:
            row.pop("_key", None)
            handle = row["author_username"].lower().lstrip("@")
            if not handle or handle in excluded:
                continue
            profile_urls = _direct_candidates(handle, platform, "accounts")
            row["author_url"] = profile_urls[0] if profile_urls else ""
            clean.append(row)
            if len(clean) >= per_platform:
                break
        return clean

    batches = await asyncio.gather(
        *[_platform_posts(platform) for platform in platforms],
        return_exceptions=True,
    )
    for platform, batch in zip(platforms, batches, strict=True):
        if isinstance(batch, Exception):
            warning = {"platform": platform, "code": "collection_failed", "message": str(batch)}
            warnings.append(warning)
            coverage[platform]["status"] = "failed"
            coverage[platform]["warnings"].append(warning["message"])
            continue
        coverage[platform]["status"] = "complete"
        coverage[platform]["candidates"] = len(batch)
        for row in batch:
            key = row["url"].lower().rstrip("/")
            if key not in seen_posts:
                seen_posts.add(key)
                rows.append(row)

    scored_rows = [
        apply_evidence_policy(
            {**row, "post_url": row.get("post_url") or row.get("url", "")},
            aliases=aliases,
            recency=recency,
        )
        for row in rows
    ]
    rows = [row for row in scored_rows if not row.get("rejected")]
    creators = aggregate_creators(rows)
    profiles: list[dict] = []
    if enrich_creators and creators:
        sem = asyncio.Semaphore(max(1, min(concurrency, 6)))

        async def _enrich_creator(creator: dict):
            if not creator["url"]:
                return
            profile = await _scrape_one(
                creator["url"], mode, timeout, sem, retries=1,
                auto_escalate=True, use_cache=True, options={}
            )
            profile = _enrich_counts(profile)
            for field in (
                "full_name", "bio", "followers", "following", "posts_count",
                "videos_count", "likes", "is_verified", "profile_pic", "error",
            ):
                if field in profile:
                    creator[field] = profile[field]
            profiles.append(creator)

        await asyncio.gather(
            *[_enrich_creator(creator) for creator in creators],
            return_exceptions=True,
        )
        creators = aggregate_creators(rows, profiles=profiles)
    rows.sort(
        key=lambda row: (
            row["platform"],
            row["author_username"].lower(),
            row["url"],
        )
    )
    for platform in platforms:
        platform_rows = [row for row in rows if row.get("platform") == platform]
        coverage[platform]["evidence"] = len(platform_rows)
        coverage[platform]["verified"] = sum(
            row.get("confidence_tier") == "verified" for row in platform_rows
        )
        if coverage[platform]["status"] == "complete" and not platform_rows:
            coverage[platform]["status"] = "limited"
            coverage[platform]["warnings"].append("No verifiable public evidence was returned")
    run_status = "partial" if warnings or any(
        item["status"] in {"failed", "limited"} for item in coverage.values()
    ) else "complete"
    return {
        "posts": rows,
        "creators": creators,
        "aliases": aliases,
        "coverage": list(coverage.values()),
        "warnings": warnings,
        "stats": {
            "posts": len(rows),
            "creators": len(creators),
            "verifiedCreators": sum(1 for creator in creators if creator.get("is_verified")),
            "verifiedEvidence": sum(row.get("confidence_tier") == "verified" for row in rows),
            "probableEvidence": sum(row.get("confidence_tier") == "probable" for row in rows),
            "unverifiedEvidence": sum(row.get("confidence_tier") == "unverified" for row in rows),
            "runStatus": run_status,
            "byPlatform": {
                platform: sum(1 for creator in creators if creator["platform"] == platform)
                for platform in platforms
            },
        },
    }


async def _discover(keyword: str | list[str], platforms: list[str], *, location: str = "",
                    per_platform: int = 15, timeout: int = 20,
                    target: str = "accounts", mentions: str = "",
                    search_providers: list[str] | None = None) -> dict[str, list[dict]]:
    """Find candidate URLs (accounts/videos/posts/hashtags) per platform."""
    import asyncio
    keywords = split_keywords(keyword, limit=12)
    if not keywords:
        return {platform: [] for platform in platforms}

    async def _one(platform: str):
        picked, seen = [], set()
        for term in keywords:
            for u in _direct_candidates(term, platform, target):
                key = u.lower().rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                picked.append({
                    "url": u,
                    "title": _username_from_url(u),
                    "snippet": "",
                    "source": "direct",
                    "sources": ["direct"],
                    "matched_keywords": [term],
                })
                if len(picked) >= per_platform:
                    return platform, picked

            local_found = _local_profile_hits(
                term, [platform], location=location,
                per_platform=per_platform, target=target,
            ).get(platform, [])
            for hit in local_found:
                key = hit.get("url", "").lower().rstrip("/")
                if key and key not in seen:
                    seen.add(key)
                    picked.append({**hit, "matched_keywords": [term]})
                elif key in seen:
                    for existing in picked:
                        if existing.get("url", "").lower().rstrip("/") == key:
                            matches = existing.setdefault("matched_keywords", [])
                            if term not in matches:
                                matches.append(term)
                            break
                if len(picked) >= per_platform:
                    return platform, picked

        raw: list[dict] = []
        for term in keywords:
            for query in _search_query_variants(platform, term, location, target, mentions):
                hits = await _search_aggregate(
                    query,
                    timeout=timeout,
                    max_results=max(per_platform * 3, 12),
                    providers=search_providers,
                )
                raw.extend({**hit, "matched_keywords": [term], "query": query} for hit in hits)
                if len(raw) >= per_platform * 10:
                    break
            if len(raw) >= per_platform * 10:
                break
        for hit in raw:
            cu = _url_filter(platform, hit.get("url", ""), target)
            key = cu.lower().rstrip("/") if cu else ""
            if key and key not in seen:
                seen.add(key)
                picked.append({**hit, "url": cu})
            elif key:
                for existing in picked:
                    if existing.get("url", "").lower().rstrip("/") == key:
                        for term in hit.get("matched_keywords", []):
                            matches = existing.setdefault("matched_keywords", [])
                            if term not in matches:
                                matches.append(term)
                        for source in hit.get("sources", [hit.get("source", "search")]):
                            sources = existing.setdefault("sources", [existing.get("source", "search")])
                            if source not in sources:
                                sources.append(source)
                        break
            if len(picked) >= per_platform:
                break
        return platform, picked

    pairs = await asyncio.gather(*[_one(p) for p in platforms], return_exceptions=True)
    found: dict[str, list[str]] = {}
    for pair in pairs:
        if isinstance(pair, Exception):
            continue
        platform, picked = pair
        found[platform] = picked
    return found


def _enrich_counts(data: dict) -> dict:
    """Fallback follower/subscriber extraction for platforms without a dedicated parser."""
    if data.get("followers") or data.get("subscribers"):
        return data
    blob = " ".join(str(data.get(k, "")) for k in ("description", "title", "text_content"))[:6000]
    m = re.search(r"([\d.,]+\s*[KMB]?)\s*(subscribers|followers|followers?|subs)\b", blob, re.I)
    if not m:
        m = re.search(r"\b(subscribers|followers|subs)\s*[:\-]?\s*([\d.,]+\s*[KMB]?)", blob, re.I)
    if m:
        first_is_label = not re.match(r"^[\d.,]", m.group(1))
        label = m.group(1 if first_is_label else 2).lower()
        val = m.group(2 if first_is_label else 1).replace(" ", "")
        if "sub" in label:
            data["subscribers"] = val
        else:
            data["followers"] = val
    return data


def _result_from_search_hit(hit: dict, platform: str, target: str,
                            scraped: dict | None = None) -> dict:
    """Build a usable influencer result from search-engine metadata only."""
    if isinstance(hit.get("local_data"), dict):
        data = _normalize_local_profile(hit["local_data"], platform)
        data.setdefault("target", target)
        data["search_only"] = True
        data["needs_deep_scrape"] = False
        data["search_source"] = hit.get("source", data.get("search_source", "local_cache"))
        data["search_sources"] = hit.get("sources", [data["search_source"]])
        data["matched_keywords"] = hit.get("matched_keywords", [])
        return _canonical_profile_fields(data)

    url = hit.get("url", "")
    title = _safe(hit.get("title", ""))
    snippet = _safe(hit.get("snippet", ""))
    username = _username_from_url(url)
    data = {
        "url": url,
        "username": username,
        "platform": platform,
        "target": target,
        "status": 200,
        "full_name": re.sub(r"\s*[\-|•|:].*$", "", title).strip()[:120],
        "title": title,
        "bio": snippet,
        "description": snippet,
        "search_source": hit.get("source", "search"),
        "search_sources": hit.get("sources", [hit.get("source", "search")]),
        "matched_keywords": hit.get("matched_keywords", []),
        "search_only": True,
        "needs_deep_scrape": True,
    }
    if scraped:
        data["scrape_status"] = scraped.get("status", 0)
        if scraped.get("error"):
            data["scrape_error"] = scraped.get("error")
        if scraped.get("response_time") is not None:
            data["response_time"] = scraped.get("response_time")
    return _canonical_profile_fields(_enrich_counts(data))


def _apply_discovery_context(data: dict, hit: dict, keywords: list[str], *,
                             target: str = "accounts", recency: str = "any") -> dict:
    """Attach provenance and a deterministic relevance score to a result."""
    data.setdefault("search_source", hit.get("source", "search"))
    data["search_sources"] = hit.get("sources", [data.get("search_source", "search")])
    data["source_count"] = len({str(source) for source in data["search_sources"] if source})
    data["source_corroborated"] = data["source_count"] > 1
    data["freshness"] = hit.get("published_at") or hit.get("date") or data.get("published_at") or ""
    data["matched_keywords"] = hit.get("matched_keywords", [])
    relevance = similarity_breakdown(
        {"bio": " ".join(keywords)},
        data,
        signals=keywords,
    )
    data["discovery_score"] = relevance["score"]
    data["match_reasons"] = relevance["reasons"]
    if target != "accounts":
        method = "search_metadata" if data.get("search_only") else "post_page"
        evidence = apply_evidence_policy(
            {
                **data,
                "post_url": data.get("url") or hit.get("url", ""),
                "author_username": data.get("username") or _post_author_from_hit(
                    data.get("platform", ""), data.get("url") or hit.get("url", ""),
                    hit.get("title", ""), hit.get("snippet", ""),
                ),
                "title": hit.get("title") or data.get("title", ""),
                "snippet": hit.get("snippet") or data.get("description", ""),
                "source": f"search:{data.get('search_source', 'web')}" if method == "search_metadata" else data.get("search_source", "post_page"),
                "provider_names": data["search_sources"],
                "verification_method": method,
            },
            aliases=expand_brand_aliases(keywords),
            recency=recency,
        )
        for field in (
            "confidence_score", "confidence_tier", "confidence_reasons",
            "matched_aliases", "match_locations", "published_at", "date_precision", "rejected",
        ):
            data[field] = evidence[field]
    return data


def _should_use_search_fallback(data: dict) -> bool:
    blob = " ".join(str(data.get(k, "")) for k in ("error", "title", "description", "text_content")).lower()
    return (
        bool(data.get("error"))
        or data.get("status") in (0, 401, 403, 429)
        or "login_required" in blob
        or "log in" in blob
        or "javascript is not available" in blob
        or "enable javascript" in blob
    )


def _search_passes(data: dict, *, min_f: int = 0, max_f: int = 0,
                   verified_only: bool = False, bio_kw: str = "",
                   target: str = "accounts") -> bool:
    if data.get("error") or data.get("status") != 200:
        return False
    if target != "accounts" and data.get("rejected"):
        return False
    # Follower / verified gates only make sense for account pages; videos, posts,
    # stories and hashtag pages don't carry their own follower count.
    if target == "accounts":
        count = _to_int_count(data.get("followers") or data.get("subscribers"))
        if min_f and count < min_f:
            return False
        if max_f and count > max_f:
            return False
        if verified_only and not data.get("is_verified"):
            return False
    if bio_kw:
        blob = " ".join(str(data.get(k, "")) for k in
                        ("bio", "description", "full_name", "title", "username")).lower()
        if bio_kw.lower() not in blob:
            return False
    return True


def _local_profile_for_url(url: str) -> dict | None:
    """Return a copy of a locally cached profile matching ``url``."""
    key = _canonical_social_url(url).lower().rstrip("/")
    for profile in _load_local_profiles():
        if str(profile.get("url", "")).lower().rstrip("/") == key:
            return dict(profile)
    return None


async def _run_lookalike(p: dict, emit=None) -> dict:
    """Run single- or multi-seed cross-platform lookalike discovery."""
    sem = asyncio.Semaphore(p["concurrency"])
    seed_profiles: list[dict] = []
    seed_urls = {_canonical_social_url(url).lower().rstrip("/") for url in p["seeds"]}

    for index, seed_url in enumerate(p["seeds"], start=1):
        seed = _local_profile_for_url(seed_url)
        if seed is None:
            seed = await _scrape_one(
                seed_url,
                p["mode"] if p["mode"] != "search_only" else "http",
                p["timeout"],
                sem,
                retries=p["retries"],
                auto_escalate=p["auto_escalate"],
                use_cache=p["use_cache"],
            )
        if not seed.get("username"):
            seed["username"] = _username_from_url(seed_url)
        seed.setdefault("url", seed_url)
        seed.setdefault("platform", _platform_of(seed_url))
        signals = derive_seed_keywords(seed, explicit=p["signals"], limit=6)
        seed["_lookalike_signals"] = signals
        seed_profiles.append(seed)
        if emit and not emit({
            "type": "seed",
            "index": index,
            "total": len(p["seeds"]),
            "seed": {
                "url": seed.get("url"),
                "username": seed.get("username"),
                "platform": seed.get("platform"),
                "signals": signals,
                "status": seed.get("status", 200),
                "error": seed.get("error", ""),
            },
        }):
            return {"seeds": seed_profiles, "results": [], "cancelled": True}

    all_signals: list[str] = []
    for seed in seed_profiles:
        for term in seed.get("_lookalike_signals", []):
            if term.casefold() not in {existing.casefold() for existing in all_signals}:
                all_signals.append(term)
    all_signals = all_signals[:12]
    if not all_signals:
        return {
            "seeds": seed_profiles,
            "results": [],
            "error": "No usable profile signals were found. Add focus keywords and try again.",
        }

    found = await _discover(
        all_signals,
        p["platforms"],
        location=p["location"],
        per_platform=p["perPlatform"],
        timeout=p["timeout"],
        target="accounts",
        search_providers=p["search_providers"],
    )
    candidates: list[tuple[str, dict]] = []
    candidate_seen: set[str] = set()
    for platform, hits in found.items():
        for hit in hits:
            key = _canonical_social_url(hit.get("url", "")).lower().rstrip("/")
            if not key or key in seed_urls or key in candidate_seen:
                continue
            candidate_seen.add(key)
            candidates.append((platform, hit))
        if emit and not emit({
            "type": "discovered",
            "platform": platform,
            "count": len(hits),
            "candidates": len(candidates),
        }):
            return {"seeds": seed_profiles, "results": [], "cancelled": True}

    async def _score_candidate(platform: str, hit: dict) -> dict:
        if p["mode"] == "search_only":
            data = _result_from_search_hit(hit, platform, "accounts")
        else:
            data = await _scrape_one(
                hit["url"],
                p["mode"],
                p["timeout"],
                sem,
                retries=p["retries"],
                auto_escalate=p["auto_escalate"],
                use_cache=p["use_cache"],
            )
            if _should_use_search_fallback(data):
                data = _result_from_search_hit(hit, platform, "accounts", data)
        data = _apply_discovery_context(_enrich_counts(data), hit, all_signals)

        best: dict | None = None
        best_seed: dict | None = None
        for seed in seed_profiles:
            score = similarity_breakdown(
                seed,
                data,
                signals=p["signals"] or seed.get("_lookalike_signals", []),
            )
            if best is None or score["score"] > best["score"]:
                best = score
                best_seed = seed
        best = best or {"score": 0, "reasons": [], "shared_topics": [], "audience_proximity": 0}
        data["similarity_score"] = best["score"]
        data["match_reasons"] = best["reasons"]
        data["shared_topics"] = best.get("shared_topics", [])
        data["audience_proximity"] = best.get("audience_proximity", 0)
        data["matched_seed"] = {
            "url": best_seed.get("url", "") if best_seed else "",
            "username": best_seed.get("username", "") if best_seed else "",
            "platform": best_seed.get("platform", "") if best_seed else "",
        }
        return data

    tasks = [
        asyncio.ensure_future(_score_candidate(platform, hit))
        for platform, hit in candidates
    ]
    results: list[dict] = []
    for done, task in enumerate(asyncio.as_completed(tasks), start=1):
        try:
            data = await task
        except Exception as exc:
            _logger.debug("Lookalike candidate failed: %s", exc)
            continue
        if data.get("similarity_score", 0) >= p["minScore"]:
            results.append(data)
        if emit and not emit({
            "type": "result",
            "done": done,
            "total": len(tasks),
            "passes": data.get("similarity_score", 0) >= p["minScore"],
            "result": data,
        }):
            for pending in tasks:
                pending.cancel()
            return {"seeds": seed_profiles, "results": results, "cancelled": True}

    results.sort(
        key=lambda row: (
            float(row.get("similarity_score", 0) or 0),
            _to_int_count(row.get("followers") or row.get("subscribers")),
            1 if row.get("is_verified") else 0,
        ),
        reverse=True,
    )
    results = results[: p["limit"]]
    return {
        "seeds": seed_profiles,
        "signals": all_signals,
        "discovered": {platform: len(hits) for platform, hits in found.items()},
        "results": results,
        "stats": {
            "seedCount": len(seed_profiles),
            "candidates": len(candidates),
            "matched": len(results),
            "averageScore": round(
                sum(float(row.get("similarity_score", 0)) for row in results) / len(results),
                1,
            ) if results else 0,
        },
    }


@cli.command()
@click.argument("username")
@click.option("--term", "-t", "terms_raw", multiple=True, required=True,
              help="Hashtag (#x) or mention (@x) to match. Repeatable; bare words match both.")
@click.option("--platform", "-p", "platforms", multiple=True,
              type=click.Choice(list(_POST_PLATFORMS)), default=("tiktok", "instagram"),
              show_default=True, help="Platforms to search. Repeatable.")
@click.option("--limit", "-l", "per_platform", default=25, show_default=True,
              help="Max URLs per platform")
@click.option("--timeout", default=20, show_default=True, help="Per-request timeout (s)")
@click.option("--proxy", default="", help="Proxy URL")
@click.option("--deep/--no-deep", default=True, show_default=True,
              help="Scan the user's public profile data for recent posts (high confidence)")
@click.option("--engines/--no-engines", default=True, show_default=True,
              help="Also query search engines for older posts (turn off for bulk runs)")
@click.option("--mode", type=click.Choice(["http", "browser", "stealth"]), default="http",
              show_default=True, help="Fallback fetch mode for the profile scan")
@click.option("--output", "-o", "output_file", default="",
              help="Write results to a file (.txt = URLs only, .json / .csv = full rows)")
@click.option("--json", "as_json", is_flag=True, help="Print full JSON instead of plain URLs")
def posts(username, terms_raw, platforms, per_platform, timeout, proxy, deep, engines,
          mode, output_file, as_json):
    """Find a user's posts/videos/reels containing a hashtag or @mention.

    Returns post URLs only — the content itself is never scraped or stored.

    \b
    Examples:
      scrape posts zachking -t "#magic" -p tiktok
      scrape posts nasa -t "@spacex" -t "#artemis" -p instagram -p youtube -o urls.txt
    """
    terms = _parse_terms(list(terms_raw))
    if not terms:
        _echo("No valid terms given (use #hashtag or @mention)", "red")
        sys.exit(1)

    found = asyncio.run(_find_user_posts(
        username, terms, list(platforms), per_platform=per_platform,
        timeout=timeout, deep=deep, engines=engines, mode=mode, proxy=proxy,
    ))
    rows = [r for p in platforms for r in found.get(p, [])]

    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for p in platforms:
            hits = found.get(p, [])
            _echo(f"[{p}] {len(hits)} match(es)", "cyan")
            for r in hits:
                mark = "+" if r["confidence"] == "high" else "~"
                click.echo(f"  {mark} {r['url']}    ({' '.join(r['matched_terms'])}, {r['source']})")
        _echo(f"Total: {len(rows)} post URL(s)", "green")

    if output_file:
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ext = out_path.suffix.lower()
        if ext == ".json":
            out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        elif ext == ".csv":
            import csv as _csv
            with out_path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = _csv.writer(fh)
                writer.writerow(["url", "platform", "username", "matched_terms", "source", "confidence"])
                for r in rows:
                    writer.writerow([r["url"], r["platform"], r["username"],
                                     " ".join(r["matched_terms"]), r["source"], r["confidence"]])
        else:
            out_path.write_text("\n".join(r["url"] for r in rows) + "\n", encoding="utf-8")
        _echo(f"Saved to {out_path}", "green")


def _dashboard_path() -> Path:
    """Locate index.html for the checkout, an editable install, or a wheel.

    The root copy is authoritative (it is what Vercel deploys); the packaged
    copy under ``scrapling_tool/web/static`` is staged at build time so an
    installed wheel can still serve the dashboard.
    """
    override = os.environ.get("SCRAPER_DASHBOARD", "").strip()
    if override:
        return Path(override).expanduser()

    local = Path(__file__).resolve().parent / "index.html"
    if local.is_file():
        return local

    try:
        # Resolve via the top-level package only. Importing
        # scrapling_tool.web.static would execute scrapling_tool.web.__init__,
        # which pulls in FastAPI and would make UI lookup fail for reasons that
        # have nothing to do with the UI.
        import scrapling_tool as _pkg

        packaged = Path(_pkg.__file__).resolve().parent / "web" / "static" / "index.html"
        if packaged.is_file():
            return packaged
    except Exception:
        pass
    return local


@cli.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8080, type=int, help="Port to listen on")
def web(host, port):
    """Launch the web interface (serves the panel + scraping API)."""
    import asyncio
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    Handler = _build_web_handler()
    ui_path = _dashboard_path()

    _echo(f"Starting web UI at http://{host}:{port}", "cyan")
    if ui_path.exists():
        _echo(f"Open http://{host}:{port}/ in your browser (serving {ui_path})", "green")
    else:
        _echo(
            "index.html was not found next to this module or inside the installed "
            "scrapling_tool package; the dashboard will return 404.",
            "red",
        )
    _echo("Press Ctrl+C to stop", "yellow")

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _echo("\nShutting down...", "yellow")
        server.shutdown()


def _build_web_handler():
    """Build the dashboard/API request handler class (used by the CLI server and WSGI hosts)."""
    import asyncio
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    ui_path = _dashboard_path()

    # --- Rate limiter (token bucket per IP) ---
    import threading as _threading

    class _RateLimiter:
        def __init__(self, rate: float = 10.0, burst: int = 30):
            self._rate = rate
            self._burst = burst
            self._buckets: dict[str, tuple[float, float]] = {}
            self._lock = _threading.Lock()

        def allow(self, ip: str) -> bool:
            now = time.monotonic()
            with self._lock:
                if ip not in self._buckets:
                    self._buckets[ip] = (self._burst - 1, now)
                    return True
                tokens, last = self._buckets[ip]
                elapsed = now - last
                tokens = min(self._burst, tokens + elapsed * self._rate)
                if tokens >= 1:
                    self._buckets[ip] = (tokens - 1, now)
                    return True
                return False

        def cleanup(self):
            """Remove stale entries older than 5 minutes."""
            now = time.monotonic()
            with self._lock:
                stale = [ip for ip, (_, last) in self._buckets.items() if now - last > 300]
                for ip in stale:
                    del self._buckets[ip]

    rate_limiter = _RateLimiter(rate=10.0, burst=30)
    MAX_BODY_SIZE = 2 * 1024 * 1024  # 2 MB request body limit

    _UI_CACHE: dict[str, tuple[bytes, float]] = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "UltraScraper/1.4"

        def log_message(self, fmt, *args):
            pass  # suppress default request logging

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, obj, status=200):
            body = _json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()

            # gzip compression
            accept_encoding = self.headers.get("Accept-Encoding", "")
            if "gzip" in accept_encoding:
                import gzip
                body = gzip.compress(body)
                self.send_header("Content-Encoding", "gzip")

            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, payload: bytes, content_type: str, filename: str = "",
                   status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self._cors()
            if filename:
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _read_body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY_SIZE:
                return {"_error": "Request body too large"}
            try:
                raw = self.rfile.read(length)
                return _json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return {"_error": "Invalid JSON"}

        def _serve_file(self, filepath: Path, content_type: str):
            now = time.monotonic()
            key = str(filepath)
            cached = _UI_CACHE.get(key)
            if cached and now - cached[1] < 5:
                payload = cached[0]
            else:
                payload = filepath.read_bytes()
                _UI_CACHE[key] = (payload, now)

            content_hash = hashlib.md5(payload).hexdigest()
            etag = f'"{content_hash}"'

            if_none_match = self.headers.get("If-None-Match", "")
            if if_none_match == etag:
                self.send_response(304)
                self._cors()
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self._cors()

            accept_encoding = self.headers.get("Accept-Encoding", "")
            if "gzip" in accept_encoding:
                import gzip
                payload = gzip.compress(payload)
                self.send_header("Content-Encoding", "gzip")

            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed_path = urlparse(self.path)
            path = parsed_path.path
            query = parse_qs(parsed_path.query)
            if path in ("/", "/index.html", "/panel"):
                if not ui_path.exists():
                    self._json({"error": "UI file not found"}, 404)
                    return
                self._serve_file(ui_path, "text/html; charset=utf-8")
            elif path == "/api/health":
                self._json({
                    "status": "ok",
                    "version": "1.4.0",
                    "modes": ["http", "browser", "stealth"],
                    "playwright": _HAS_PLAYWRIGHT,
                    "search": True,
                    "searchPlatforms": ["tiktok", "instagram", "snapchat", "youtube", "twitter"],
                    "postFinderPlatforms": list(_POST_PLATFORMS),
                    "parsers": ["tiktok", "instagram", "snapchat", "youtube", "twitter", "generic"],
                    "features": [
                        "auto_escalate", "rate_limiting", "url_validation",
                        "result_cache", "multi_query_discovery", "post_finder",
                        "single_bulk_lookalikes", "explainable_similarity",
                        "brand_mention_creator_discovery", "native_tiktok_post_search",
                        "authorized_tiktok_audience_extraction",
                        "audience_progress_and_exports",
                    ],
                    "ui": ui_path.name if ui_path.exists() else None,
                })
            elif path == "/api/history":
                try:
                    with _db_lock:
                        conn = sqlite3.connect(str(_DB_PATH), timeout=5)
                        try:
                            cursor = conn.cursor()
                            cursor.execute(
                                "SELECT url, mode, timestamp, response_time, status, error, "
                                "platform, username, data FROM scrapes "
                                "ORDER BY timestamp DESC LIMIT 200"
                            )
                            scrapes = []
                            for r in cursor.fetchall():
                                try:
                                    cached_data = json.loads(r[8]) if r[8] else {}
                                except (TypeError, ValueError):
                                    cached_data = {}
                                cached_data.update({
                                    "url": cached_data.get("url") or r[0],
                                    "profile_url": cached_data.get("profile_url") or r[0],
                                    "mode": r[1],
                                    "timestamp": r[2],
                                    "response_time": r[3],
                                    "status": r[4],
                                    "error": r[5],
                                    "platform": cached_data.get("platform") or r[6],
                                    "username": cached_data.get("username") or r[7],
                                })
                                scrapes.append(_canonical_profile_fields(cached_data))
                            cursor.execute("SELECT type, timestamp, query, results_count, details FROM history ORDER BY timestamp DESC LIMIT 100")
                            history = [
                                {
                                    "type": r[0],
                                    "timestamp": r[1],
                                    "query": r[2],
                                    "results_count": r[3],
                                    "details": json.loads(r[4])
                                }
                                for r in cursor.fetchall()
                            ]
                            self._json({"scrapes": scrapes, "history": history})
                        finally:
                            conn.close()
                except Exception as e:
                    self._json({"error": str(e)}, 500)
            elif path == "/api/stats":
                try:
                    db_size = 0
                    if _DB_PATH.exists():
                        db_size = _DB_PATH.stat().st_size
                    with _db_lock:
                        conn = sqlite3.connect(str(_DB_PATH), timeout=5)
                        try:
                            cursor = conn.cursor()
                            cursor.execute("SELECT COUNT(*) FROM scrapes")
                            total_scrapes = cursor.fetchone()[0]
                            cursor.execute("SELECT COUNT(*) FROM scrapes WHERE status = 200 AND (error IS NULL OR error = '')")
                            successful_scrapes = cursor.fetchone()[0]
                            cursor.execute("SELECT COUNT(*) FROM history")
                            history_count = cursor.fetchone()[0]
                        finally:
                            conn.close()
                except Exception:
                    total_scrapes = 0
                    successful_scrapes = 0
                    history_count = 0
                self._json({
                    "db_size_bytes": db_size,
                    "total_scrapes": total_scrapes,
                    "successful_scrapes": successful_scrapes,
                    "history_count": history_count,
                    "cache_max": _CACHE_MAX,
                    "memory_cache_size": len(_RESULT_CACHE),
                    "playwright_installed": _HAS_PLAYWRIGHT,
                    "scrapling_installed": _HAS_FETCHERS,
                    "platform": sys.platform,
                    "python_version": sys.version,
                    "time": time.time()
                })
            elif path == "/api/audience/run":
                run_id = str((query.get("id") or [""])[0])
                try:
                    limit = int((query.get("limit") or ["100"])[0])
                    offset = int((query.get("offset") or ["0"])[0])
                except ValueError:
                    limit, offset = 100, 0
                search = str((query.get("search") or [""])[0])
                snapshot = _audience_run_snapshot(
                    run_id,
                    limit=limit,
                    offset=offset,
                    search=search,
                )
                if snapshot is None:
                    self._json({"error": "Audience run not found."}, 404)
                else:
                    self._json(snapshot)
            elif path == "/api/audience/latest":
                username = str((query.get("username") or [""])[0]).strip().lstrip("@")
                platform = str((query.get("platform") or ["tiktok"])[0]).lower()
                snapshot = _audience_latest_run(username, platform)
                self._json({"run": snapshot})
            elif path == "/api/audience/export":
                run_id = str((query.get("id") or [""])[0])
                fmt = str((query.get("format") or ["csv"])[0]).lower()
                run, rows = _audience_all_rows(run_id)
                if not run:
                    self._json({"error": "Audience run not found."}, 404)
                    return
                columns = [
                    "Profile URL", "Full Name", "Username", "Profile Category",
                    "License", "Biography", "Followers", "Following", "Likes",
                    "Avg Views", "Emails", "Phone Numbers", "Country", "City",
                    "TikTok Links", "Snapchat Links", "Twitter Links",
                    "Instagram Links", "Other Links", "Is Business", "Is Private",
                    "Is Verified", "Videos", "TikTok User ID", "Collected At",
                ]
                rows = [{
                    "Profile URL": row.get("profile_url", ""),
                    "Full Name": row.get("full_name", ""),
                    "Username": row.get("username", ""),
                    "Profile Category": "",
                    "License": "",
                    "Biography": "",
                    "Followers": row.get("followers", 0),
                    "Following": row.get("following", 0),
                    "Likes": row.get("likes", 0),
                    "Avg Views": "",
                    "Emails": "",
                    "Phone Numbers": "",
                    "Country": "",
                    "City": "",
                    "TikTok Links": row.get("profile_url", ""),
                    "Snapchat Links": "",
                    "Twitter Links": "",
                    "Instagram Links": "",
                    "Other Links": "",
                    "Is Business": "",
                    "Is Private": bool(row.get("is_private")),
                    "Is Verified": bool(row.get("is_verified")),
                    "Videos": row.get("videos", 0),
                    "TikTok User ID": row.get("user_id", ""),
                    "Collected At": row.get("collected_at", ""),
                } for row in rows]
                filename = f"{run['platform']}_{run['username']}_followers"
                if fmt == "json":
                    payload = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
                    self._bytes(payload, "application/json; charset=utf-8", f"{filename}.json")
                elif fmt == "md":
                    def md_value(value):
                        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
                    lines = [
                        f"# @{run['username']} followers",
                        "",
                        f"Collected records: {len(rows):,}",
                        "",
                        "| " + " | ".join(columns) + " |",
                        "| " + " | ".join("---" for _ in columns) + " |",
                    ]
                    lines.extend(
                        "| " + " | ".join(md_value(row.get(column)) for column in columns) + " |"
                        for row in rows
                    )
                    self._bytes(
                        "\n".join(lines).encode("utf-8"),
                        "text/markdown; charset=utf-8",
                        f"{filename}.md",
                    )
                elif fmt == "txt":
                    blocks = []
                    for index, row in enumerate(rows, start=1):
                        blocks.append("\n".join(
                            [f"Follower {index}"]
                            + [f"{column}: {row.get(column, '')}" for column in columns]
                        ))
                    self._bytes(
                        "\n\n---\n\n".join(blocks).encode("utf-8"),
                        "text/plain; charset=utf-8",
                        f"{filename}.txt",
                    )
                else:
                    import csv as _csv
                    buffer = io.StringIO()
                    writer = _csv.DictWriter(buffer, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(rows)
                    self._bytes(
                        ("\ufeff" + buffer.getvalue()).encode("utf-8"),
                        "text/csv; charset=utf-8",
                        f"{filename}.csv",
                    )
            else:
                self._json({"error": "not found"}, 404)

        # --- shared request parsing ---
        def _scrape_params(self):
            req = self._read_body()
            if "_error" in req:
                return [], "http", 20, 30, 0, False, True, {}
            urls = []
            seen_urls = set()
            for u in req.get("urls", []):
                cleaned = _canonical_social_url(str(u))
                valid, result = _validate_url(cleaned)
                key = cleaned.lower().rstrip("/")
                if valid and key not in seen_urls:
                    seen_urls.add(key)
                    urls.append(result)

            # Extract advanced options
            adv_options = {
                "proxy": str(req.get("proxy", "") or "").strip(),
                "headless": bool(req.get("headless", True) if "headless" in req else req.get("sfHeadless", True)),
                "disable_resources": bool(req.get("disable_resources", False) or req.get("sfNoResources", False)),
                "block_ads": bool(req.get("block_ads", False)),
                "network_idle": bool(req.get("network_idle", False)),
                "wait": max(0, min(int(req.get("wait", 0) or 0), 30000)),
                "wait_selector": str(req.get("wait_selector", "") or "").strip(),
                "locale": str(req.get("locale", "") or "").strip(),
                "real_chrome": bool(req.get("real_chrome", False)),
                "useragent": str(req.get("useragent", "") or "").strip(),
                "dns_over_https": bool(req.get("dns_over_https", False)),

                # Stealth specific
                "solve_cloudflare": bool(req.get("solve_cloudflare", False) or req.get("sfSolveCF", False)),
                "hide_canvas": bool(req.get("hide_canvas", False)),
                "block_webrtc": bool(req.get("block_webrtc", False)),
                "allow_webgl": bool(req.get("allow_webgl", True) if "allow_webgl" in req else True),

                # HTTP specific
                "method": str(req.get("method", "GET")).upper(),
                "headers": req.get("headers"),
                "data": req.get("data"),
                "json_data": req.get("json_data"),
                "impersonate": str(req.get("impersonate", "chrome")).lower(),
                "stealthy_headers": bool(req.get("stealthy_headers", True) if "stealthy_headers" in req else True),
                "follow_redirects": bool(req.get("follow_redirects", True) if "follow_redirects" in req else True),
                "verify": bool(req.get("verify", True) if "verify" in req else True),
                "http3": bool(req.get("http3", False)),
            }

            return (
                urls,
                req.get("mode", "http"),
                max(1, min(int(req.get("concurrency", 20) or 20), 50)),
                max(5, min(int(req.get("timeout", 30) or 30), 120)),
                max(0, min(int(req.get("retries", 2) or 0), 5)),
                bool(req.get("auto_escalate", False)),
                bool(req.get("use_cache", True)),
                adv_options
            )

        def do_POST(self):
            # Rate limit check
            client_ip = self.client_address[0]
            if not rate_limiter.allow(client_ip):
                self._json({"error": "Rate limit exceeded. Try again shortly."}, 429)
                return
            path = urlparse(self.path).path
            try:
                if path == "/api/scrape":
                    self._handle_batch()
                elif path == "/api/scrape/stream":
                    self._handle_stream()
                elif path == "/api/search":
                    self._handle_search()
                elif path == "/api/search/stream":
                    self._handle_search_stream()
                elif path == "/api/lookalike":
                    self._handle_lookalike()
                elif path == "/api/lookalike/stream":
                    self._handle_lookalike_stream()
                elif path == "/api/posts":
                    self._handle_posts()
                elif path == "/api/posts/stream":
                    self._handle_posts_stream()
                elif path == "/api/audience/run":
                    req = self._read_body()
                    target_url = _canonical_social_url(str(
                        req.get("target_url")
                        or req.get("url")
                        or "https://www.tiktok.com/@roxashop"
                    ))
                    platform = _platform_of(target_url)
                    username = _username_from_url(target_url).lstrip("@")
                    if platform != "tiktok" or not username:
                        self._json({
                            "error": "Authorized follower extraction currently supports TikTok profile URLs."
                        }, 400)
                        return
                    try:
                        max_followers = max(0, int(req.get("max_followers") or 0))
                    except (TypeError, ValueError):
                        max_followers = 0
                    run_id, started = _start_audience_job(target_url, max_followers)
                    self._json({
                        "run_id": run_id,
                        "started": started,
                        "run": _audience_run_snapshot(run_id),
                    }, 202 if started else 200)
                elif path == "/api/audience/cancel":
                    req = self._read_body()
                    run_id = str(req.get("run_id") or "")
                    with _AUDIENCE_JOBS_LOCK:
                        job = _AUDIENCE_JOBS.get(run_id)
                        if job:
                            job["cancel"].set()
                    if not job:
                        snapshot = _audience_run_snapshot(run_id, limit=1)
                        if snapshot is None:
                            self._json({"error": "Audience run not found."}, 404)
                            return
                    self._json({"status": "cancelling", "run_id": run_id})
                elif path == "/api/history/delete":
                    req = self._read_body()
                    url_to_delete = req.get("url")
                    with _db_lock:
                        conn = sqlite3.connect(str(_DB_PATH), timeout=5)
                        try:
                            cursor = conn.cursor()
                            cursor.execute("DELETE FROM scrapes WHERE url = ?", (url_to_delete,))
                            conn.commit()
                            self._json({"status": "ok"})
                        finally:
                            conn.close()
                elif path == "/api/history/clear":
                    with _db_lock:
                        conn = sqlite3.connect(str(_DB_PATH), timeout=5)
                        try:
                            cursor = conn.cursor()
                            cursor.execute("DELETE FROM scrapes")
                            cursor.execute("DELETE FROM history")
                            conn.commit()
                            self._json({"status": "ok"})
                        finally:
                            conn.close()
                else:
                    self._json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass  # client went away mid-response
            except Exception as e:  # noqa: BLE001
                try:
                    self._json({"error": str(e)[:300]}, 500)
                except Exception:
                    pass

        # --- non-streaming batch (backward compatible) ---
        def _handle_batch(self):
            urls, mode, concurrency, timeout, retries, auto_escalate, use_cache, options = self._scrape_params()
            if not urls:
                self._json({"results": [], "stats": _scrape_stats([])})
                return

            async def _run():
                sem = asyncio.Semaphore(concurrency)
                return await asyncio.gather(
                    *[_scrape_one(u, mode, timeout, sem, retries=retries, auto_escalate=auto_escalate, use_cache=use_cache, options=options) for u in urls]
                )

            results = asyncio.run(_run())
            try:
                _db_add_history("bulk", f"{len(urls)} URLs", len([r for r in results if r.get("status") == 200]), {"mode": mode, "concurrency": concurrency})
            except Exception:
                pass
            self._json({"results": results, "stats": _scrape_stats(results)})

        # --- streaming: emit one NDJSON line per URL as it completes ---
        def _handle_stream(self):
            urls, mode, concurrency, timeout, retries, auto_escalate, use_cache, options = self._scrape_params()

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            if not urls:
                self.wfile.write((_json.dumps({"type": "complete", "stats": _scrape_stats([])}) + "\n").encode("utf-8"))
                return

            def emit(obj) -> bool:
                """Write one NDJSON event; return False if the client hung up."""
                try:
                    self.wfile.write((_json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return False

            emit({"type": "start", "total": len(urls), "mode": mode})

            async def _run():
                sem = asyncio.Semaphore(concurrency)

                async def _wrapped(idx, url):
                    data = await _scrape_one(url, mode, timeout, sem, retries=retries, auto_escalate=auto_escalate, use_cache=use_cache, options=options)
                    return idx, url, data

                tasks = [asyncio.ensure_future(_wrapped(i, u)) for i, u in enumerate(urls)]
                done = 0
                all_results = []
                alive = True
                for fut in asyncio.as_completed(tasks):
                    idx, url, data = await fut
                    all_results.append(data)
                    done += 1
                    if alive:
                        metric = (
                            data.get("subscribers") if data.get("platform") == "snapchat"
                            else data.get("followers")
                        )
                        alive = emit({
                            "type": "result",
                            "index": idx,
                            "done": done,
                            "total": len(urls),
                            "result": data,
                            "metric": metric or "",
                            "ok": data.get("status") == 200 and not data.get("error"),
                        })
                        if not alive:
                            for t in tasks:
                                t.cancel()
                            break
                if alive:
                    try:
                        _db_add_history("bulk_stream", f"{len(urls)} URLs", len([r for r in all_results if r.get("status") == 200]), {"mode": mode, "concurrency": concurrency})
                    except Exception:
                        pass
                    emit({"type": "complete", "stats": _scrape_stats(all_results)})

            asyncio.run(_run())

        # --- influencer search params ---
        def _search_params(self):
            req = self._read_body()
            plats = [
                p for p in req.get("platforms", [])
                if p in SUPPORTED_PLATFORMS
            ] or ["tiktok", "instagram"]
            target = str(req.get("target", "accounts")).strip().lower()
            if target not in TARGETS:
                target = "accounts"
            keywords = split_keywords(req.get("keywords") or req.get("keyword"), limit=12)
            # Parse search providers request; None means auto-detect all available
            raw_providers = req.get("searchProviders") or req.get("search_providers")
            search_providers = None
            if isinstance(raw_providers, list) and raw_providers:
                valid = {"bing", "duckduckgo", "querit", "tavily", "serper", "duckduckgo_api"}
                search_providers = [p for p in raw_providers if p in valid] or None
            return {
                "keyword": " · ".join(keywords),
                "keywords": keywords,
                "platforms": plats,
                "target": target,
                "location": str(req.get("location", "")).strip(),
                "mentions": str(req.get("mentions", "")).strip(),
                "search_providers": search_providers,
                "perPlatform": max(1, min(int(req.get("perPlatform", 15) or 15), 40)),
                "limit": max(1, min(int(req.get("limit", 100) or 100), 500)),
                "minFollowers": _to_int_count(req.get("minFollowers", 0)),
                "maxFollowers": _to_int_count(req.get("maxFollowers", 0)),
                "verifiedOnly": bool(req.get("verifiedOnly", False)),
                "bioKeyword": str(req.get("bioKeyword", "") or req.get("bio_keyword", "")).strip(),
                "mode": (
                    req.get("mode", "http")
                    if req.get("mode", "http") in {"search_only", "http", "browser", "stealth"}
                    else "http"
                ),
                "concurrency": max(1, min(int(req.get("concurrency", 6) or 6), 12)),
                "timeout": max(5, min(int(req.get("timeout", 30) or 30), 120)),
                "retries": max(0, min(int(req.get("retries", 1) or 0), 5)),
                "auto_escalate": bool(req.get("auto_escalate", False)),
                "use_cache": bool(req.get("use_cache", True)),
                "recency": normalize_recency(req.get("recency", "any")),
            }

        @staticmethod
        def _rank(collected):
            matched = [d for d, ok in collected if ok]
            matched.sort(
                key=lambda d: (
                    float(d.get("discovery_score", 0) or 0),
                    _to_int_count(d.get("followers") or d.get("subscribers")),
                    1 if d.get("is_verified") else 0,
                    1 if d.get("profile_pic") else 0,
                    0 if d.get("error") else 1,
                ),
                reverse=True,
            )
            return matched

        # --- non-streaming influencer search ---
        def _handle_search(self):
            p = self._search_params()
            if not p["keywords"]:
                self._json({"error": "Add at least one discovery keyword."}, 400)
                return

            async def _run():
                found = await _discover(
                    p["keywords"], p["platforms"], location=p["location"],
                    per_platform=p["perPlatform"], timeout=p["timeout"],
                    target=p["target"], mentions=p["mentions"],
                    search_providers=p["search_providers"],
                )
                candidates = [(plat, hit) for plat, lst in found.items() for hit in lst]
                sem = asyncio.Semaphore(p["concurrency"])

                async def _enrich(plat, hit):
                    if p["mode"] == "search_only":
                        data = _result_from_search_hit(hit, plat, p["target"])
                        return _apply_discovery_context(data, hit, p["keywords"], target=p["target"], recency=p["recency"])
                    data = await _scrape_one(hit["url"], p["mode"], p["timeout"], sem, retries=p["retries"], auto_escalate=p["auto_escalate"], use_cache=p["use_cache"])
                    if _should_use_search_fallback(data):
                        data = _result_from_search_hit(hit, plat, p["target"], data)
                        return _apply_discovery_context(data, hit, p["keywords"], target=p["target"], recency=p["recency"])
                    data = _enrich_counts(data)
                    data.setdefault("platform", plat)
                    data.setdefault("target", p["target"])
                    return _apply_discovery_context(data, hit, p["keywords"], target=p["target"], recency=p["recency"])

                results = await asyncio.gather(*[_enrich(plat, hit) for plat, hit in candidates])
                collected = [
                    (d, _search_passes(
                        d, min_f=p["minFollowers"], max_f=p["maxFollowers"],
                        verified_only=p["verifiedOnly"], bio_kw=p["bioKeyword"],
                        target=p["target"]))
                    for d in results
                ]
                ranked = self._rank(collected)[: p["limit"]]
                return found, results, ranked

            found, results, ranked = asyncio.run(_run())
            try:
                _db_add_history("search", p["keyword"], len(ranked), {"platforms": p["platforms"], "target": p["target"]})
            except Exception:
                pass

            by_plat = {}
            for d in ranked:
                k = d.get("platform", "other")
                by_plat[k] = by_plat.get(k, 0) + 1
            self._json({
                "results": ranked,
                "discovered": {k: len(v) for k, v in found.items()},
                "stats": {
                    "scanned": len(results), "matched": len(ranked),
                    "candidates": sum(len(v) for v in found.values()),
                    "byPlatform": by_plat, "avgTime": _scrape_stats(results)["avgTime"],
                },
            })

        # --- streaming influencer search (discovery + live enrichment) ---
        def _handle_search_stream(self):
            p = self._search_params()

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(obj) -> bool:
                try:
                    self.wfile.write((_json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return False

            emit({"type": "start", "phase": "discovery",
                  "platforms": p["platforms"], "keyword": p["keyword"],
                  "keywords": p["keywords"]})

            async def _run():
                if not p["keywords"]:
                    emit({"type": "error", "message": "Add at least one discovery keyword."})
                    return
                found = await _discover(
                    p["keywords"], p["platforms"], location=p["location"],
                    per_platform=p["perPlatform"], timeout=p["timeout"],
                    target=p["target"], mentions=p["mentions"],
                    search_providers=p["search_providers"],
                )
                candidates = []
                for plat, lst in found.items():
                    candidates.extend((plat, hit) for hit in lst)
                    if not emit({"type": "discovered", "platform": plat,
                                 "count": len(lst), "candidates": len(candidates)}):
                        return

                emit({"type": "phase", "phase": "enrich", "total": len(candidates)})
                if not candidates:
                    emit({"type": "complete", "stats": {
                        "scanned": 0, "matched": 0, "candidates": 0,
                        "byPlatform": {}, "avgTime": 0}})
                    return

                sem = asyncio.Semaphore(p["concurrency"])

                async def _wrapped(plat, hit):
                    if p["mode"] == "search_only":
                        data = _result_from_search_hit(hit, plat, p["target"])
                        return plat, _apply_discovery_context(data, hit, p["keywords"], target=p["target"], recency=p["recency"])
                    data = await _scrape_one(hit["url"], p["mode"], p["timeout"], sem, retries=p["retries"], auto_escalate=p["auto_escalate"], use_cache=p["use_cache"])
                    if _should_use_search_fallback(data):
                        data = _result_from_search_hit(hit, plat, p["target"], data)
                        return plat, _apply_discovery_context(data, hit, p["keywords"], target=p["target"], recency=p["recency"])
                    data = _enrich_counts(data)
                    data.setdefault("platform", plat)
                    data.setdefault("target", p["target"])
                    return plat, _apply_discovery_context(data, hit, p["keywords"], target=p["target"], recency=p["recency"])

                tasks = [asyncio.ensure_future(_wrapped(plat, hit)) for plat, hit in candidates]
                done, collected, alive = 0, [], True
                for fut in asyncio.as_completed(tasks):
                    plat, data = await fut
                    done += 1
                    passes = _search_passes(
                        data, min_f=p["minFollowers"], max_f=p["maxFollowers"],
                        verified_only=p["verifiedOnly"], bio_kw=p["bioKeyword"],
                        target=p["target"])
                    collected.append((data, passes))
                    if alive:
                        alive = emit({
                            "type": "result", "done": done, "total": len(candidates),
                            "result": data, "platform": data.get("platform", plat),
                            "passes": passes,
                            "count": _to_int_count(data.get("followers") or data.get("subscribers")),
                            "ok": data.get("status") == 200 and not data.get("error"),
                        })
                        if not alive:
                            for t in tasks:
                                t.cancel()
                            break

                if alive:
                    ranked = self._rank(collected)[: p["limit"]]
                    by_plat = {}
                    for d in ranked:
                        k = d.get("platform", "other")
                        by_plat[k] = by_plat.get(k, 0) + 1
                    try:
                        _db_add_history("search_stream", p["keyword"], len(ranked), {"platforms": p["platforms"], "target": p["target"]})
                    except Exception:
                        pass
                    emit({"type": "complete", "stats": {
                        "scanned": len(collected), "matched": len(ranked),
                        "candidates": len(candidates), "byPlatform": by_plat,
                        "avgTime": _scrape_stats([d for d, _ in collected])["avgTime"],
                    }, "ranked": ranked})

            asyncio.run(_run())

        # --- lookalike discovery params ---------------------------------
        def _lookalike_params(self):
            req = self._read_body()
            raw_seeds = req.get("seeds") or req.get("seed") or []
            if isinstance(raw_seeds, str):
                raw_seeds = re.split(r"[\r\n,;]+", raw_seeds)
            seeds: list[str] = []
            seen: set[str] = set()
            for raw in raw_seeds if isinstance(raw_seeds, list) else []:
                canonical = _canonical_social_url(str(raw or "").strip())
                valid, normalized = _validate_url(canonical)
                key = canonical.lower().rstrip("/")
                if valid and key not in seen:
                    seen.add(key)
                    seeds.append(normalized)
                if len(seeds) >= 50:
                    break

            platforms = [
                platform for platform in (req.get("platforms") or [])
                if platform in SUPPORTED_PLATFORMS
            ] or list(SUPPORTED_PLATFORMS)
            raw_providers = req.get("searchProviders") or req.get("search_providers")
            search_providers = None
            if isinstance(raw_providers, list) and raw_providers:
                valid_providers = {
                    "bing", "duckduckgo", "querit", "tavily", "serper", "duckduckgo_api"
                }
                search_providers = [
                    provider for provider in raw_providers if provider in valid_providers
                ] or None
            mode = str(req.get("mode", "http"))
            if mode not in {"search_only", "http", "browser", "stealth"}:
                mode = "http"
            return {
                "seeds": seeds,
                "platforms": platforms,
                "signals": split_keywords(req.get("signals") or req.get("keywords"), limit=12),
                "location": str(req.get("location", "") or "").strip(),
                "search_providers": search_providers,
                "perPlatform": max(1, min(int(req.get("perPlatform", 12) or 12), 40)),
                "limit": max(1, min(int(req.get("limit", 100) or 100), 500)),
                "minScore": max(0.0, min(float(req.get("minScore", 20) or 0), 100.0)),
                "mode": mode,
                "concurrency": max(1, min(int(req.get("concurrency", 5) or 5), 12)),
                "timeout": max(5, min(int(req.get("timeout", 30) or 30), 120)),
                "retries": max(0, min(int(req.get("retries", 1) or 0), 5)),
                "auto_escalate": bool(req.get("auto_escalate", True)),
                "use_cache": bool(req.get("use_cache", True)),
            }

        def _handle_lookalike(self):
            p = self._lookalike_params()
            if not p["seeds"]:
                self._json({"error": "Add at least one valid public profile URL."}, 400)
                return
            payload = asyncio.run(_run_lookalike(p))
            if payload.get("error"):
                self._json(payload, 422)
                return
            try:
                _db_add_history(
                    "lookalike",
                    f"{len(p['seeds'])} seed profile(s)",
                    len(payload.get("results", [])),
                    {"platforms": p["platforms"], "signals": payload.get("signals", [])},
                )
            except Exception:
                pass
            self._json(payload)

        def _handle_lookalike_stream(self):
            p = self._lookalike_params()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(obj) -> bool:
                try:
                    self.wfile.write(
                        (_json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
                    )
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return False

            if not p["seeds"]:
                emit({"type": "error", "message": "Add at least one valid public profile URL."})
                return
            emit({
                "type": "start",
                "seedCount": len(p["seeds"]),
                "platforms": p["platforms"],
            })
            payload = asyncio.run(_run_lookalike(p, emit=emit))
            if payload.get("cancelled"):
                return
            if payload.get("error"):
                emit({"type": "error", "message": payload["error"]})
                return
            try:
                _db_add_history(
                    "lookalike_stream",
                    f"{len(p['seeds'])} seed profile(s)",
                    len(payload.get("results", [])),
                    {"platforms": p["platforms"], "signals": payload.get("signals", [])},
                )
            except Exception:
                pass
            emit({
                "type": "complete",
                "stats": payload["stats"],
                "signals": payload["signals"],
                "ranked": payload["results"],
            })

        # --- post finder params ---
        def _posts_params(self):
            req = self._read_body()
            usernames = req.get("usernames")
            if not isinstance(usernames, list):
                usernames = [req.get("username", "")]
            usernames = [str(u).strip() for u in usernames if str(u or "").strip()]
            brand_seeds = req.get("brandSeeds") or req.get("brand_seeds") or []
            if not isinstance(brand_seeds, list):
                brand_seeds = [brand_seeds]
            brand_seeds = [
                str(seed).strip() for seed in brand_seeds
                if str(seed or "").strip()
            ][:25]
            discover_creators = bool(
                req.get("discoverCreators", req.get("discover_creators", False))
            )
            terms = _parse_terms(req.get("terms") or req.get("term") or "")
            excluded_handles: set[str] = set()
            if discover_creators:
                derived_labels: list[str] = []
                for seed in brand_seeds:
                    handle = (
                        _username_from_url(_canonical_social_url(seed))
                        if "://" in seed or "www." in seed.lower()
                        else seed.lstrip("@").strip()
                    )
                    handle = re.sub(r"[^\w.\-]", "", handle)
                    if not handle:
                        continue
                    excluded_handles.add(handle.lower())
                    derived_labels.extend((f"@{handle}", f"#{handle}", handle))
                terms = _parse_terms(
                    [_term_label(term) for term in terms] + derived_labels
                )
            plats = [p for p in (req.get("platforms") or []) if p in _POST_PLATFORMS]
            raw_providers = req.get("searchProviders") or req.get("search_providers")
            search_providers = None
            if isinstance(raw_providers, list) and raw_providers:
                valid = {"bing", "duckduckgo", "querit", "tavily", "serper", "duckduckgo_api"}
                search_providers = [p for p in raw_providers if p in valid] or None
            return {
                "usernames": usernames[:500],
                "brandSeeds": brand_seeds,
                "discoverCreators": discover_creators,
                "excludedHandles": excluded_handles,
                "enrichCreators": bool(req.get("enrichCreators", True)),
                "terms": terms,
                "platforms": plats or (list(_POST_PLATFORMS) if discover_creators else ["tiktok", "instagram"]),
                "perPlatform": max(1, min(int(req.get("perPlatform", 25) or 25), 100)),
                "deep": bool(req.get("deep", True)),
                "engines": bool(req.get("engines", req.get("useSearchEngines", True))),
                "mode": str(req.get("mode", "http")),
                "timeout": max(5, min(int(req.get("timeout", 20) or 20), 120)),
                "proxy": str(req.get("proxy", "") or ""),
                "concurrency": max(1, min(int(req.get("concurrency", 6) or 6), 20)),
                "search_providers": search_providers,
                "recency": normalize_recency(req.get("recency", "any")),
                "confidenceTiers": [
                    tier for tier in (req.get("confidenceTiers") or CONFIDENCE_TIERS)
                    if tier in CONFIDENCE_TIERS
                ] or list(CONFIDENCE_TIERS),
                "verifyEvidence": bool(req.get("verifyEvidence", True)),
                "useOptionalApis": bool(req.get("useOptionalApis", True)),
            }

        @staticmethod
        def _posts_stats(rows):
            by_plat, by_conf = {}, {}
            for r in rows:
                by_plat[r.get("platform", "other")] = by_plat.get(r.get("platform", "other"), 0) + 1
                by_conf[r.get("confidence", "medium")] = by_conf.get(r.get("confidence", "medium"), 0) + 1
            return {"total": len(rows), "byPlatform": by_plat, "byConfidence": by_conf}

        # --- non-streaming post finder ---
        def _handle_posts(self):
            p = self._posts_params()
            if p["discoverCreators"]:
                if not p["brandSeeds"] or not p["terms"]:
                    self._json({
                        "error": "Add a brand handle/URL and at least one mention or hashtag."
                    }, 400)
                    return

                payload = asyncio.run(_find_brand_mentions(
                    p["terms"], p["platforms"],
                    per_platform=p["perPlatform"], timeout=p["timeout"],
                    proxy=p["proxy"], mode=p["mode"],
                    search_providers=p["search_providers"],
                    excluded_handles=p["excludedHandles"],
                    enrich_creators=p["enrichCreators"],
                    concurrency=p["concurrency"],
                    recency=p["recency"],
                    aliases=expand_brand_aliases(
                        p["brandSeeds"], [_term_label(term) for term in p["terms"]]
                    ),
                ))
                payload["posts"] = [
                    row for row in payload["posts"]
                    if row.get("confidence_tier") in p["confidenceTiers"]
                ]
                payload["creators"] = aggregate_creators(payload["posts"], payload["creators"])
                try:
                    _db_add_history(
                        "brand_mentions",
                        ", ".join(p["brandSeeds"][:3]),
                        len(payload["creators"]),
                        {
                            "terms": [_term_label(term) for term in p["terms"]],
                            "platforms": p["platforms"],
                            "posts": len(payload["posts"]),
                        },
                    )
                except Exception:
                    pass
                self._json(payload)
                return

            if not p["usernames"] or not p["terms"]:
                self._json({"error": "username(s) and at least one #hashtag/@mention term required"}, 400)
                return

            async def _run():
                sem = asyncio.Semaphore(p["concurrency"])

                async def _bounded(u):
                    async with sem:
                        return await _find_user_posts(
                            u, p["terms"], p["platforms"],
                            per_platform=p["perPlatform"], timeout=p["timeout"],
                            deep=p["deep"], engines=p["engines"], mode=p["mode"],
                            proxy=p["proxy"], search_providers=p["search_providers"])

                founds = await asyncio.gather(
                    *[_bounded(u) for u in p["usernames"]], return_exceptions=True)
                rows = []
                for found in founds:
                    if isinstance(found, Exception):
                        continue
                    for lst in found.values():
                        rows.extend(lst)
                return rows

            rows = asyncio.run(_run())
            try:
                _db_add_history("posts", ", ".join(p["usernames"][:3]), len(rows), {"terms": [str(t) for t in p["terms"]], "platforms": p["platforms"]})
            except Exception:
                pass
            self._json({
                "results": rows,
                "urls": [r["url"] for r in rows],
                "stats": self._posts_stats(rows),
            })

        # --- streaming post finder: one NDJSON line per (username, platform) ---
        def _handle_posts_stream(self):
            p = self._posts_params()

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(obj) -> bool:
                try:
                    self.wfile.write((_json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return False

            if p["discoverCreators"]:
                if not p["brandSeeds"] or not p["terms"]:
                    emit({
                        "type": "complete",
                        "total": 0,
                        "urls": [],
                        "creators": [],
                        "error": "Add a brand handle/URL and at least one mention or hashtag.",
                    })
                    return
                emit({
                    "type": "start",
                    "workflow": "brand_mentions",
                    "brandSeeds": p["brandSeeds"],
                    "platforms": p["platforms"],
                    "terms": [_term_label(term) for term in p["terms"]],
                })
                aliases = expand_brand_aliases(
                    p["brandSeeds"], [_term_label(term) for term in p["terms"]]
                )
                emit({
                    "type": "plan", "aliases": aliases, "recency": p["recency"],
                    "platforms": p["platforms"], "confidenceTiers": p["confidenceTiers"],
                })

                async def _run_brand_mentions():
                    payload = await _find_brand_mentions(
                        p["terms"], p["platforms"],
                        per_platform=p["perPlatform"], timeout=p["timeout"],
                        proxy=p["proxy"], mode=p["mode"],
                        search_providers=p["search_providers"],
                        excluded_handles=p["excludedHandles"],
                        enrich_creators=p["enrichCreators"],
                        concurrency=p["concurrency"],
                        recency=p["recency"],
                        aliases=aliases,
                    )
                    payload["posts"] = [
                        row for row in payload["posts"]
                        if row.get("confidence_tier") in p["confidenceTiers"]
                    ]
                    payload["creators"] = aggregate_creators(payload["posts"], payload["creators"])
                    for coverage_item in payload.get("coverage", []):
                        if not emit({"type": "platform", **coverage_item}):
                            return
                    for warning in payload.get("warnings", []):
                        if not emit({"type": "warning", **warning}):
                            return
                    for evidence in payload["posts"]:
                        if not emit({"type": "evidence", "evidence": evidence}):
                            return
                    for index, creator in enumerate(payload["creators"], 1):
                        if not emit({
                            "type": "creator",
                            "done": index,
                            "total": len(payload["creators"]),
                            "creator": creator,
                        }):
                            return
                    emit({
                        "type": "complete",
                        "total": len(payload["posts"]),
                        "urls": [row["url"] for row in payload["posts"]],
                        "posts": payload["posts"],
                        "creators": payload["creators"],
                        "stats": payload["stats"],
                        "coverage": payload.get("coverage", []),
                        "warnings": payload.get("warnings", []),
                        "aliases": payload.get("aliases", aliases),
                    })
                    try:
                        _db_add_history(
                            "brand_mentions_stream",
                            ", ".join(p["brandSeeds"][:3]),
                            len(payload["creators"]),
                            {
                                "terms": [_term_label(term) for term in p["terms"]],
                                "platforms": p["platforms"],
                                "posts": len(payload["posts"]),
                            },
                        )
                    except Exception:
                        pass

                asyncio.run(_run_brand_mentions())
                return

            if not p["usernames"] or not p["terms"]:
                emit({"type": "complete", "total": 0, "urls": [],
                      "error": "username(s) and at least one #hashtag/@mention term required"})
                return

            emit({"type": "start", "usernames": p["usernames"],
                  "platforms": p["platforms"],
                  "terms": [_term_label(t) for t in p["terms"]]})

            async def _run():
                async def _pair(u, plat):
                    found = await _find_user_posts(
                        u, p["terms"], [plat], per_platform=p["perPlatform"],
                        timeout=p["timeout"], deep=p["deep"], mode=p["mode"],
                        proxy=p["proxy"], search_providers=p["search_providers"])
                    return u, plat, found.get(plat, [])

                tasks = [asyncio.ensure_future(_pair(u, plat))
                         for u in p["usernames"] for plat in p["platforms"]]
                done, rows = 0, []
                for fut in asyncio.as_completed(tasks):
                    try:
                        u, plat, lst = await fut
                    except Exception:
                        done += 1
                        continue
                    done += 1
                    rows.extend(lst)
                    if not emit({"type": "result", "username": u, "platform": plat,
                                 "done": done, "total": len(tasks),
                                 "count": len(lst), "results": lst}):
                        for t in tasks:
                            t.cancel()
                        return
                emit({"type": "complete", "total": len(rows),
                      "urls": [r["url"] for r in rows],
                      "stats": self._posts_stats(rows)})
                try:
                    _db_add_history("posts_stream", ", ".join(p["usernames"][:3]), len(rows), {"terms": [str(t) for t in p["terms"]], "platforms": p["platforms"]})
                except Exception:
                    pass

            asyncio.run(_run())

    return Handler


def serve_dashboard() -> None:
    """Console entry point that exposes the complete operational dashboard."""
    web.main(
        args=sys.argv[1:],
        prog_name="scraper-serve",
        standalone_mode=True,
    )


# ----- MAIN -----

if __name__ == "__main__":
    cli()
