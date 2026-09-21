"""Politeness and proxy rotation for outbound fetches.

Two concerns that a scraper gets wrong by default:

**Rate.** Concurrency is set globally, so a run over 200 TikTok URLs at
concurrency 20 puts 20 simultaneous requests on one host. That is what earns a
block, and the block then looks like a parser bug. The limiter here is
per-domain, so a mixed run stays fast overall while no single host sees a burst.

**Proxy choice.** A single proxy string means one bad exit node fails the whole
run. The pool tracks which proxies actually work, orders by measured latency,
and takes a failing one out of rotation after repeated errors rather than
retrying into it forever.

Both are process-local. That is the right scope: they exist to shape what this
process sends, and a shared limiter would need coordination the tool has no
reason to carry.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

_log = logging.getLogger(__name__)

__all__ = ["DomainRateLimiter", "ProxyPool", "ProxyEntry", "check_proxy", "domain_of"]


def domain_of(url: str) -> str:
    """Registrable-ish host for rate-limiting purposes.

    Strips ``www.`` and the leading ``m.``/``mobile.`` variants so that
    ``m.tiktok.com`` and ``www.tiktok.com`` share one bucket — they share one
    rate limiter upstream too.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class DomainRateLimiter:
    """Async token bucket, one bucket per domain.

    A bucket rather than a fixed sleep so that a run touching a host once every
    few seconds pays nothing, while a burst against the same host is spread out.
    ``burst`` is how many requests may go out back to back before the rate
    starts to bite.
    """

    def __init__(self, rate_per_sec: float = 2.0, burst: int = 4) -> None:
        self.rate = max(0.01, float(rate_per_sec))
        self.burst = max(1, int(burst))
        self._tokens: dict[str, float] = {}
        self._updated: dict[str, float] = {}
        self._overrides: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = threading.Lock()

    def set_domain_rate(self, domain: str, rate_per_sec: float) -> None:
        """Override the rate for one host — e.g. slower for a known-touchy CDN."""
        with self._guard:
            self._overrides[domain.lower()] = max(0.01, float(rate_per_sec))

    def rate_for(self, domain: str) -> float:
        with self._guard:
            return self._overrides.get(domain, self.rate)

    def _lock_for(self, domain: str) -> asyncio.Lock:
        with self._guard:
            lock = self._locks.get(domain)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[domain] = lock
            return lock

    async def acquire(self, url: str) -> float:
        """Wait until a request to ``url`` is allowed. Returns seconds waited."""
        domain = domain_of(url)
        if not domain:
            return 0.0

        rate = self.rate_for(domain)
        waited = 0.0

        async with self._lock_for(domain):
            now = time.monotonic()
            last = self._updated.get(domain, now)
            tokens = min(
                float(self.burst),
                self._tokens.get(domain, float(self.burst)) + (now - last) * rate,
            )

            if tokens < 1.0:
                delay = (1.0 - tokens) / rate
                # Cap a single wait so a misconfigured rate cannot stall a run
                # for minutes with no way to tell what is happening.
                delay = min(delay, 30.0)
                await asyncio.sleep(delay)
                waited = delay
                tokens = min(float(self.burst), tokens + delay * rate)

            self._tokens[domain] = tokens - 1.0
            self._updated[domain] = time.monotonic()

        return waited

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Current bucket state, for the anti-block console."""
        with self._guard:
            domains = set(self._tokens) | set(self._overrides)
            return {
                d: {
                    "tokens": round(self._tokens.get(d, float(self.burst)), 2),
                    "rate": self._overrides.get(d, self.rate),
                }
                for d in sorted(domains)
            }


# ---------------------------------------------------------------------------
# Proxy pool
# ---------------------------------------------------------------------------
@dataclass
class ProxyEntry:
    url: str
    label: str = ""
    enabled: bool = True
    status: str = "unknown"   # unknown | ok | down
    latency_ms: int = 0
    ok_count: int = 0
    fail_count: int = 0
    last_error: str = ""
    last_used: float = field(default=0.0)

    @property
    def healthy(self) -> bool:
        return self.enabled and self.status != "down"

    @property
    def success_rate(self) -> float:
        total = self.ok_count + self.fail_count
        return round(self.ok_count / total * 100, 1) if total else 0.0


class ProxyPool:
    """Round-robin over healthy proxies, ordered by measured latency.

    Round-robin rather than always-fastest: hammering the single best exit node
    is exactly the traffic pattern that gets an IP flagged, which defeats the
    purpose of having a pool.
    """

    # Consecutive failures before a proxy is benched. Three, because a single
    # failure is usually the target site rather than the proxy, and two in a row
    # still happens on a flaky-but-usable node.
    FAIL_THRESHOLD = 3

    def __init__(self, entries: list[ProxyEntry] | None = None) -> None:
        self._entries: list[ProxyEntry] = list(entries or [])
        self._streak: dict[str, int] = {}
        self._cycle = itertools.count()
        self._guard = threading.Lock()

    def replace(self, entries: list[ProxyEntry]) -> None:
        with self._guard:
            self._entries = list(entries)
            self._streak = {e.url: self._streak.get(e.url, 0) for e in entries}

    def all(self) -> list[ProxyEntry]:
        with self._guard:
            return list(self._entries)

    def healthy(self) -> list[ProxyEntry]:
        with self._guard:
            live = [e for e in self._entries if e.healthy]
        # Unknown-status proxies sort with the known-good ones rather than last:
        # a fresh pool has no measurements yet and would otherwise never be used.
        return sorted(live, key=lambda e: (e.latency_ms or 1_000))

    def next_proxy(self) -> str:
        """The next proxy URL to use, or "" when the pool is empty or all down."""
        live = self.healthy()
        if not live:
            return ""
        entry = live[next(self._cycle) % len(live)]
        entry.last_used = time.time()
        return entry.url

    def record(self, proxy_url: str, ok: bool, error: str = "") -> bool:
        """Log an outcome. Returns True when this call benched the proxy."""
        if not proxy_url:
            return False
        with self._guard:
            entry = next((e for e in self._entries if e.url == proxy_url), None)
            if entry is None:
                return False

            if ok:
                entry.ok_count += 1
                entry.status = "ok"
                entry.last_error = ""
                self._streak[proxy_url] = 0
                return False

            entry.fail_count += 1
            entry.last_error = error[:300]
            streak = self._streak.get(proxy_url, 0) + 1
            self._streak[proxy_url] = streak
            if streak >= self.FAIL_THRESHOLD:
                entry.status = "down"
                _log.warning(
                    "proxy benched after %d consecutive failures: %s (%s)",
                    streak, proxy_url, error[:120],
                )
                return True
            return False


async def check_proxy(proxy_url: str, timeout: float = 12.0,
                      probe: str = "https://httpbin.org/ip") -> tuple[bool, int, str]:
    """Probe one proxy. Returns ``(ok, latency_ms, error)``.

    Probes a plain endpoint rather than a target site so the result says
    something about the proxy rather than about whether that site blocks it.
    """
    import httpx

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url, timeout=timeout, follow_redirects=True
        ) as client:
            response = await client.get(probe)
        elapsed = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            return False, elapsed, f"HTTP {response.status_code}"
        return True, elapsed, ""
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return False, elapsed, f"{type(exc).__name__}: {exc}"[:300]
