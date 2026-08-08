"""Ordered provider fallback with telemetry.

``pick_provider`` answers "who is best for this URL" and returns exactly one
provider. That is the wrong shape when the best provider is rate-limited,
out of credit, or simply blocked on this particular host: the fetch fails and
the operator sees an error, even though a second provider would have worked.

This module turns the pick into a *ranked list* and walks it until something
returns usable content, recording what each attempt cost. The record is what
feeds the Providers page — an operator can see that Tavily answered 40ms and
ScrapeGraph timed out, rather than guessing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from scrapling_tool.providers.base import (
    FetchResult,
    Provider,
    ProviderError,
    list_providers,
    pick_provider,
)

_log = logging.getLogger(__name__)

__all__ = ["candidates_for", "fetch_with_fallback", "set_recorder", "provider_health"]

# The webapp installs a recorder so calls land in the provider_stats table.
# Left unset, the chain still works — it just keeps no history.
_Recorder = Callable[[str, bool, int], None]
_recorder: _Recorder | None = None


def set_recorder(fn: _Recorder | None) -> None:
    global _recorder
    _recorder = fn


def _record(provider: str, ok: bool, elapsed_ms: int) -> None:
    if _recorder is None:
        return
    try:
        _recorder(provider, ok, elapsed_ms)
    except Exception:  # telemetry must never break a fetch
        _log.debug("provider telemetry failed", exc_info=True)


# A response can be HTTP 200 and still be worthless: a login wall, a captcha
# interstitial, or an SPA shell that never hydrated. Treating those as success
# is what makes a run report "200 OK" over a page with no data in it.
_BLOCK_MARKERS = (
    "captcha",
    "are you a robot",
    "unusual traffic",
    "access denied",
    "please enable javascript",
    "log in to continue",
    "sign up to see",
)

# Below this, a "successful" HTML response is a shell rather than a page.
_MIN_USEFUL_BYTES = 500


def looks_blocked(result: FetchResult) -> bool:
    """True when a 2xx response carries no usable content."""
    if result.status in (401, 403, 407, 429) or result.status >= 500:
        return True
    if result.status == 200 and len(result.body) < _MIN_USEFUL_BYTES and not result.meta:
        return True

    sample = result.text[:4000].lower()
    return any(marker in sample for marker in _BLOCK_MARKERS)


def candidates_for(url: str, *, hint: str | None = None,
                   exclude: set[str] | None = None) -> list[Provider]:
    """Providers to try for ``url``, best first.

    The smart pick leads; everything else that is available and willing follows
    in priority order. Nothing is tried twice.
    """
    exclude = exclude or set()
    ordered: list[Provider] = []
    seen: set[str] = set()

    def add(provider: Provider | None) -> None:
        if provider is None or provider.name in seen or provider.name in exclude:
            return
        try:
            if not provider.available() or not provider.can_handle(url):
                return
        except Exception:
            return
        seen.add(provider.name)
        ordered.append(provider)

    try:
        add(pick_provider(url, hint=hint))
    except ProviderError:
        pass

    for provider in list_providers():
        add(provider)

    return ordered


def fetch_with_fallback(
    url: str,
    *,
    hint: str | None = None,
    max_attempts: int = 3,
    exclude: set[str] | None = None,
    **opts: Any,
) -> tuple[FetchResult | None, list[dict]]:
    """Try providers in order until one returns usable content.

    Returns ``(result, attempts)``. ``result`` is None when every candidate
    failed; ``attempts`` always describes what was tried, in order, so a caller
    can surface the reason rather than a bare failure.
    """
    attempts: list[dict] = []
    providers = candidates_for(url, hint=hint, exclude=exclude)[: max(1, max_attempts)]

    if not providers:
        return None, [{"provider": "", "ok": False, "error": "no provider available", "ms": 0}]

    for provider in providers:
        started = time.monotonic()
        try:
            result = provider.fetch(url, **opts)
        except ProviderError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            _record(provider.name, False, elapsed)
            attempts.append({"provider": provider.name, "ok": False,
                             "error": str(exc)[:300], "ms": elapsed})
            continue
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            _record(provider.name, False, elapsed)
            attempts.append({"provider": provider.name, "ok": False,
                             "error": f"{type(exc).__name__}: {exc}"[:300], "ms": elapsed})
            continue

        elapsed = int((time.monotonic() - started) * 1000)
        blocked = looks_blocked(result)
        _record(provider.name, not blocked, elapsed)
        attempts.append({
            "provider": provider.name,
            "ok": not blocked,
            "status": result.status,
            "bytes": len(result.body),
            "error": "blocked or empty response" if blocked else "",
            "ms": elapsed,
        })

        if not blocked:
            result.meta.setdefault("attempts", attempts)
            return result, attempts

    return None, attempts


def provider_health() -> list[dict]:
    """One row per registered provider, for the Providers page."""
    from scrapling_tool import credentials

    rows: list[dict] = []
    for provider in list_providers():
        try:
            available = bool(provider.available())
        except Exception as exc:
            available = False
            _log.debug("provider %s availability check failed: %s", provider.name, exc)

        needs_key = provider.name in credentials.PROVIDERS
        rows.append({
            "name": provider.name,
            "priority": provider.priority,
            "free": bool(provider.free),
            "available": available,
            "needs_key": needs_key,
            "configured": bool(credentials.get_key(provider.name)) if needs_key else True,
            "masked_key": credentials.mask(credentials.get_key(provider.name)) if needs_key else "",
            "key_source": credentials.source_of(provider.name) if needs_key else "n/a",
        })
    return rows
