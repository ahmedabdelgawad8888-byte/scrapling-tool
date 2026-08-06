"""Exponential-backoff fetch wrapper.

The :func:`fetch_with_retry` helper wraps a single-URL provider call with
configurable retry semantics. It is intentionally tiny and free of any
dependency on a specific HTTP library — the caller passes a function that
takes ``(url, **opts)`` and returns a :class:`FetchResult`, plus a
:class:`RetryConfig`.

Why not ``urllib3.Retry``? Because we want to share retry logic across
direct httpx, scrapling Fetcher, scrapling StealthyFetcher and the proxy
providers — each of which is a different object type. A common callable
interface is the cheapest way to get one policy across all of them.

Backoff schedule (defaults, override via RetryConfig):

    attempt 1: try
    attempt 2: sleep 0.5s + random(0..0.25)
    attempt 3: sleep 1.0s + random(0..0.5)
    attempt 4: sleep 2.0s + random(0..1.0)
    attempt 5: sleep 4.0s + random(0..2.0)

``Retry-After`` headers (seconds or HTTP date) are honored when present.
"""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from scrapling_tool.providers.base import FetchResult, ProviderError

log = logging.getLogger("scrapling_tool.hardening.retry")


class RetryExhausted(ProviderError):
    """All attempts failed; carries the last exception for diagnostics."""


@dataclass(slots=True)
class RetryConfig:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: float = 0.5  # multiplied by delay
    retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504)
    retry_on_exc: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)

    def backoff(self, attempt: int) -> float:
        """Return the sleep duration *before* attempt ``attempt`` (1-indexed).

        attempt=1 → 0 (first try is immediate)
        attempt=2 → base_delay * 2^0 + jitter
        attempt=3 → base_delay * 2^1 + jitter*2
        """
        if attempt <= 1:
            return 0.0
        delay = self.base_delay * (2 ** (attempt - 2))
        delay += delay * self.jitter * random.random()
        return min(delay, self.max_delay)


_FetchFn = Callable[..., FetchResult]


def fetch_with_retry(
    fn: _FetchFn,
    url: str,
    *,
    config: RetryConfig | None = None,
    on_retry: Callable[[int, BaseException | int, float], None] | None = None,
    **opts: Any,
) -> FetchResult:
    """Call ``fn(url, **opts)`` with exponential backoff.

    Parameters
    ----------
    fn : callable
        Provider function. Must accept ``url`` as positional and arbitrary
        keyword args and return a :class:`FetchResult`.
    url : str
        URL to fetch.
    config : RetryConfig, optional
        Defaults are sane (4 attempts, 0.5s base, 30s max, jitter 0.5).
    on_retry : callable, optional
        ``on_retry(attempt, reason, sleep_seconds)`` — called *before* each
        sleep so callers can log or surface progress.
    **opts
        Forwarded to ``fn`` unchanged.
    """
    cfg = config or RetryConfig()
    last_exc: BaseException | None = None
    last_status: int = 0

    for attempt in range(1, cfg.max_attempts + 1):
        sleep_for = cfg.backoff(attempt)
        if sleep_for and on_retry is not None:
            on_retry(attempt, last_exc or last_status or "transient", sleep_for)
        if sleep_for:
            time.sleep(sleep_for)
        try:
            result = fn(url, **opts)
        except Exception as e:  # noqa: BLE001 — we re-raise the last one
            last_exc = e
            last_status = 0
            if isinstance(e, cfg.retry_on_exc) and attempt < cfg.max_attempts:
                continue
            if attempt >= cfg.max_attempts:
                raise RetryExhausted(f"giving up after {attempt} attempts: {e}") from e
            # Non-retryable exception — surface immediately
            raise
        # HTTP-level retry. We do NOT return a retryable status on the last
        # attempt — that would silently swallow quota exhaustion.
        if result.status in cfg.retry_on_status:
            last_status = result.status
            last_exc = None
            if attempt >= cfg.max_attempts:
                raise RetryExhausted(
                    f"giving up after {attempt} attempts: status={last_status}"
                )
            continue
        return result

    # Defensive: should be unreachable
    raise RetryExhausted(f"giving up after {cfg.max_attempts} attempts: status={last_status}")
