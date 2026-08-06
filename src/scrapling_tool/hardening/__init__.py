"""scrapling_tool.hardening — retry/backoff, robots.txt, crawl-delay, cache.

Four pieces, all importable independently:

- :func:`fetch_with_retry` — exponential backoff with jitter, respects
  ``Retry-After``, surfaces quota exhaustion distinctly from network errors
- :class:`RobotsCache` — per-host robots.txt with TTL; honors
  ``Crawl-delay`` and ``User-agent`` allow/deny
- :class:`ResponseCache` — disk-backed HTTP cache keyed on
  ``(url, etag, last-modified)``; honors 304 Not Modified
- :class:`ProxyRotator` — round-robin proxy rotation through a user-supplied
  pool of HTTP/HTTPS proxies

All four are *opt-in* via CLI flags and config; the existing
``scrape http`` path stays identical when none are set.
"""
from scrapling_tool.hardening.cache import CacheEntry, ResponseCache
from scrapling_tool.hardening.proxy import ProxyRotator
from scrapling_tool.hardening.retry import (
    RetryConfig,
    RetryExhausted,
    fetch_with_retry,
)
from scrapling_tool.hardening.robots import RobotsCache, RobotsDecision

__all__ = [
    "fetch_with_retry",
    "RetryConfig",
    "RetryExhausted",
    "RobotsCache",
    "RobotsDecision",
    "ResponseCache",
    "CacheEntry",
    "ProxyRotator",
]
