"""Cheap/free provider layer for fetching and parsing.

A *Provider* is a single function ``fetch(url, **opts) -> FetchResult`` that
returns a normalized FetchResult regardless of upstream implementation:

- ``DirectProvider``  — plain HTTP via httpx (free, no quota)
- ``ScraplingProvider`` — wraps scrapling's Fetcher / StealthyFetcher / DynamicFetcher
- ``ScraperAPIProvider`` — ScraperAPI free tier (1000 calls / month)
- ``ScrapingbeeProvider`` — Scrapingbee free trial (1000 credits)
- ``SitemapProvider``  — fetches ``/sitemap.xml`` and yields discovered URLs
- ``RssProvider``      — fetches RSS/Atom feed and yields items
- ``JsonLdProvider``   — extracts ``application/ld+json`` blocks from a page
- ``TikTokOembedProvider`` — official TikTok oEmbed (free, no auth)
- ``YouTubeOembedProvider`` — official YouTube oEmbed (free, no auth)

Routing is *automatic* via :func:`pick_provider` based on URL shape and
configured free-tier credentials. The whole point: prefer the legitimate
free path (oEmbed, RSS, sitemap, JSON-LD) before paying for proxy bytes.
"""

# Importing each module runs the `register(...)` calls at the bottom of
# every provider file. This is the single canonical discovery point —
# without these imports the registry is empty and `pick_provider` raises.
from scrapling_tool.providers import (
    direct,  # noqa: F401
    jsonld,  # noqa: F401
    oembed,  # noqa: F401
    rss,  # noqa: F401
    scraperapi,  # noqa: F401
    scrapingbee,  # noqa: F401
    scrapling_provider,  # noqa: F401
    sitemap,  # noqa: F401
)
from scrapling_tool.providers.base import (
    FetchResult,
    Provider,
    ProviderError,
    get_provider,
    list_providers,
    pick_provider,
    register,
)

__all__ = [
    "FetchResult",
    "Provider",
    "ProviderError",
    "register",
    "get_provider",
    "list_providers",
    "pick_provider",
]
