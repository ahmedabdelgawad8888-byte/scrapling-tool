"""Disk-backed HTTP cache keyed on (url, etag, last-modified).

Goals:
- Single SQLite database in the user's ``HERMES_HOME``-ish cache dir
- Honest conditional requests: store ``etag``/``last-modified`` from the
  first response, send ``If-None-Match``/``If-Modified-Since`` on the next,
  and treat 304 as a cache hit (no body transferred)
- Bounded size: oldest entries are pruned when the file grows past a limit

The cache is *opt-in* via the CLI's ``--cache`` flag and via the
``ResponseCache.fetch_via_cache`` helper.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrapling_tool.providers.base import FetchResult


@dataclass(slots=True)
class CacheEntry:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    etag: str | None
    last_modified: str | None
    cached_at: float
    provider: str = ""


class ResponseCache:
    """SQLite-backed response cache with conditional-request support."""

    DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 512 MB

    def __init__(self, path: os.PathLike[str] | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                url_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status INTEGER NOT NULL,
                headers TEXT NOT NULL,
                body BLOB NOT NULL,
                etag TEXT,
                last_modified TEXT,
                cached_at REAL NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_responses_cached_at ON responses(cached_at)")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ResponseCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def get(self, url: str) -> CacheEntry | None:
        row = self._conn.execute(
            "SELECT url, status, headers, body, etag, last_modified, cached_at "
            "FROM responses WHERE url_hash = ?",
            (self._key(url),),
        ).fetchone()
        if not row:
            return None
        url_v, status, headers, body, etag, last_modified, cached_at = row
        return CacheEntry(
            url=url_v,
            status=status,
            headers=json.loads(headers),
            body=body,
            etag=etag,
            last_modified=last_modified,
            cached_at=cached_at,
        )

    def put(self, entry: CacheEntry) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO responses "
            "(url_hash, url, status, headers, body, etag, last_modified, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._key(entry.url),
                entry.url,
                entry.status,
                json.dumps(entry.headers),
                entry.body,
                entry.etag,
                entry.last_modified,
                entry.cached_at,
            ),
        )

    def fetch_via_cache(
        self,
        url: str,
        *,
        fetcher: Callable[..., FetchResult] | None = None,
        force: bool = False,
    ) -> FetchResult:
        """Wrap a fetcher call with conditional-request logic.

        On hit, returns the cached body with ``from_cache=True``.
        On miss, calls ``fetcher(url, **kwargs)`` and stores the result.
        On 304, returns the cached body and refreshes the cached_at.
        """
        existing = None if force else self.get(url)
        if existing is not None:
            kwargs: dict[str, Any] = {}
            if existing.etag:
                kwargs["if_none_match"] = existing.etag
            if existing.last_modified:
                kwargs["if_modified_since"] = existing.last_modified
            kwargs.setdefault("timeout", 30)
            fresh = (fetcher or _default_fetcher)(url, **kwargs)
            if fresh.status == 304:
                # Refresh timestamp, return cached body
                self.put(
                    CacheEntry(
                        url=existing.url,
                        status=existing.status,
                        headers=existing.headers,
                        body=existing.body,
                        etag=existing.etag,
                        last_modified=existing.last_modified,
                        cached_at=time.time(),
                    )
                )
                return FetchResult(
                    url=existing.url,
                    final_url=existing.url,
                    body=existing.body,
                    status=existing.status,
                    headers=existing.headers,
                    from_cache=True,
                    provider="cache",
                )
            if fresh.status == 200:
                self._store(fresh)
                return fresh
            return fresh  # 4xx/5xx — pass through, don't cache
        if force or existing is None:
            fresh = (fetcher or _default_fetcher)(url, timeout=30)
            if fresh.status == 200:
                self._store(fresh)
            return fresh
        return existing  # unreachable, satisfies type checkers

    def _store(self, result: FetchResult) -> None:
        etag = result.headers.get("etag") or result.headers.get("ETag")
        last_modified = result.headers.get("last-modified") or result.headers.get("Last-Modified")
        self.put(
            CacheEntry(
                url=result.url,
                status=result.status,
                headers=result.headers,
                body=result.body,
                etag=etag,
                last_modified=last_modified,
                cached_at=time.time(),
                provider=result.provider,
            )
        )

    def prune(self, max_bytes: int = DEFAULT_MAX_BYTES) -> int:
        """Drop oldest entries until the on-disk size is below ``max_bytes``."""
        size = self._path.stat().st_size
        if size <= max_bytes:
            return 0
        deleted = 0
        for row in self._conn.execute("SELECT url_hash FROM responses ORDER BY cached_at ASC"):
            self._conn.execute("DELETE FROM responses WHERE url_hash = ?", (row[0],))
            deleted += 1
            size = self._path.stat().st_size
            if size <= max_bytes:
                break
        return deleted


def _default_fetcher(url: str, **kwargs: Any) -> FetchResult:
    from scrapling_tool.providers.direct import DirectProvider

    return DirectProvider().fetch(url, **kwargs)
