"""Provider ABC + registry + smart picker."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import urlparse


class ProviderError(RuntimeError):
    """Raised when a provider cannot satisfy a fetch request."""


@dataclass(slots=True)
class FetchResult:
    """Normalized fetch result, independent of upstream.

    ``body`` is bytes; ``text`` is decoded UTF-8 (errors='replace'); ``status``
    is the HTTP status (0 if the provider is offline, e.g. an RSS feed parse);
    ``headers`` is the upstream headers dict (may be empty); ``from_cache`` is
    True when served by the local response cache; ``meta`` is provider-specific
    data (e.g. discovered URLs from a sitemap feed).
    """

    url: str
    final_url: str
    body: bytes
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    from_cache: bool = False
    provider: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text and self.body:
            self.text = self.body.decode("utf-8", errors="replace")


class Provider(ABC):
    """Abstract base for all providers."""

    name: ClassVar[str] = ""
    priority: ClassVar[int] = 100  # lower = tried first
    free: ClassVar[bool] = True

    @abstractmethod
    def fetch(self, url: str, **opts: Any) -> FetchResult:
        """Fetch ``url`` and return a normalized result.

        Implementations should raise :class:`ProviderError` on any
        non-recoverable failure (auth missing, quota exhausted, parse
        failure). The :mod:`scrapling_tool.hardening` retry layer handles
        transient errors.
        """

    def can_handle(self, url: str) -> bool:
        """Return True if this provider is willing to try ``url``."""
        return True

    def available(self) -> bool:
        """Return True if the provider is configured (credentials, etc.)."""
        return True


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    """Register a provider instance under its ``name``."""
    if not provider.name:
        raise ValueError("provider must set .name")
    _REGISTRY[provider.name] = provider
    return provider


def get_provider(name: str) -> Provider:
    return _REGISTRY[name]


def list_providers() -> list[Provider]:
    """Return registered providers sorted by priority (lowest first)."""
    return sorted(_REGISTRY.values(), key=lambda p: p.priority)


# --------- smart picker ---------

_TIKTOK_HOSTS = re.compile(r"(?:^|\.)tiktok\.com$", re.I)
_YT_HOSTS = re.compile(r"(?:^|\.)(youtube\.com|youtu\.be)$", re.I)
_INSTAGRAM_HOSTS = re.compile(r"(?:^|\.)instagram\.com$", re.I)
_TWITTER_HOSTS = re.compile(r"(?:^|\.)(twitter\.com|x\.com)$", re.I)


def pick_provider(url: str, *, hint: str | None = None) -> Provider:
    """Pick the best provider for ``url``.

    Strategy:
    1. If the caller passes ``hint`` and that provider is registered, use it.
    2. If a free-tier credential is configured for a proxy provider
       (ScraperAPI/Scrapingbee) and the URL is a "hard" target (Instagram,
       Twitter/X), prefer the proxy provider.
    3. Otherwise, prefer a discovery provider that returns multiple items
       (RSS / sitemap / JSON-LD) when the URL looks like a feed.
    4. Fall back to the local :class:`ScraplingProvider`.

    Free-first ordering: oEmbed > RSS > sitemap > JSON-LD > scrapling > proxy.
    """
    if hint and hint in _REGISTRY:
        return _REGISTRY[hint]

    parsed_lower = url.lower()
    host = (urlparse(url).hostname or "").lower()

    if _YT_HOSTS.search(host):
        yt = _REGISTRY.get("youtube_oembed")
        if yt and yt.available():
            return yt
    if _TIKTOK_HOSTS.search(host):
        tt = _REGISTRY.get("tiktok_oembed")
        if tt and tt.available():
            return tt

    if parsed_lower.endswith("sitemap.xml") or "/sitemap" in parsed_lower:
        sm = _REGISTRY.get("sitemap")
        if sm:
            return sm
    if parsed_lower.endswith((".xml", "/feed", "/rss", "/atom")):
        rss_p = _REGISTRY.get("rss")
        if rss_p:
            return rss_p

    # Hard-target proxy fallback — only when explicitly opted in
    if _INSTAGRAM_HOSTS.search(host) or _TWITTER_HOSTS.search(host):
        for candidate in ("scrapingbee", "scraperapi"):
            p = _REGISTRY.get(candidate)
            if p and p.available():
                return p

    scrapling_p = _REGISTRY.get("scrapling")
    if scrapling_p:
        return scrapling_p

    # Absolute fallback: any registered provider
    if _REGISTRY:
        return next(iter(list_providers()))
    raise ProviderError("no providers registered")
