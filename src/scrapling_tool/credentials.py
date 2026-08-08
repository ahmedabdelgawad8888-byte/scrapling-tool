"""Single source of truth for provider API keys.

Before this module the same four keys were hardcoded as fallback defaults in
five places — two provider modules' constants, ``config.py``, ``config.yaml``
and ``ultra_scraper`` — which meant every one of them was a live credential
sitting in version control, and rotating a key meant finding all five.

Resolution order, first non-empty wins:

1. A runtime override, set by the dashboard from the ``provider_keys`` table.
   This is what makes the Providers page able to change a key without a
   restart or a file edit.
2. Environment, including historical aliases so an existing ``.env`` keeps
   working.
3. Empty — the provider reports itself unavailable and the fallback chain
   skips it. Never a baked-in default.
"""

from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_OVERRIDES: dict[str, str] = {}

# Canonical provider name -> environment variables to consult, in order.
# The misspelled SCRAPGRAPH_API_KEY is retained because it is what the existing
# .env actually uses; dropping it would silently disable the provider.
ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "tavily": ("TAVILY_API_KEY",),
    "serper": ("SERPER_API_KEY",),
    "serpapi": ("SERPAPI_API_KEY", "SERP_API_KEY"),
    "scrapegraph": (
        "SCRAPEGRAPH_API_KEY",
        "SCRAPGRAPH_API_KEY",
        "SCRAPEGRAPHAI_API_KEY",
    ),
    "scraperapi": ("SCRAPERAPI_KEY", "SCRAPER_API_KEY"),
    "scrapingbee": ("SCRAPINGBEE_API_KEY",),
    "querit": ("QUERIT_API_KEY",),
}

PROVIDERS = tuple(ENV_ALIASES)


def get_key(provider: str) -> str:
    """The active key for ``provider``, or "" when it has none configured."""
    name = provider.strip().lower()

    with _LOCK:
        override = _OVERRIDES.get(name, "")
    if override:
        return override

    for var in ENV_ALIASES.get(name, ()):
        value = (os.getenv(var) or "").strip()
        if value:
            return value

    return ""


def set_override(provider: str, key: str) -> None:
    """Install (or, with an empty key, clear) a runtime override."""
    name = provider.strip().lower()
    with _LOCK:
        if key and key.strip():
            _OVERRIDES[name] = key.strip()
        else:
            _OVERRIDES.pop(name, None)


def load_overrides(mapping: dict[str, str]) -> None:
    """Replace every override at once — used at boot from stored settings."""
    with _LOCK:
        _OVERRIDES.clear()
        for name, key in mapping.items():
            if key and key.strip():
                _OVERRIDES[name.strip().lower()] = key.strip()


def source_of(provider: str) -> str:
    """Where the active key came from: ``override``, ``env`` or ``none``.

    The Providers page shows this so an operator can tell why a key they set in
    the UI is or isn't the one being used.
    """
    name = provider.strip().lower()
    with _LOCK:
        if _OVERRIDES.get(name):
            return "override"
    for var in ENV_ALIASES.get(name, ()):
        if (os.getenv(var) or "").strip():
            return "env"
    return "none"


def mask(key: str) -> str:
    """A key rendered safe to send to a browser.

    Keeps enough of the head and tail to identify which credential this is
    without transmitting a usable one — the dashboard never receives a full key
    back, only what the operator typed in.
    """
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:5]}{'•' * 8}{key[-4:]}"


def status() -> dict[str, dict[str, str | bool]]:
    """Per-provider credential state, safe for the API to return."""
    out: dict[str, dict[str, str | bool]] = {}
    for name in PROVIDERS:
        key = get_key(name)
        out[name] = {
            "configured": bool(key),
            "masked": mask(key),
            "source": source_of(name),
        }
    return out
