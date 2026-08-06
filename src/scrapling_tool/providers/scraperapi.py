"""ScraperAPI provider — 1,000 free API calls per month.

Auth: set ``SCRAPERAPI_KEY`` in env. Used as the proxy fallback for hard
targets (Instagram, Twitter/X) when oEmbed / RSS / sitemap don't apply.

Pricing note: even the free tier is rate-limited to ~5 req/min on a rolling
window. The hardening retry layer honors 429 with backoff; this module just
builds the URL.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx

from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register


class ScraperAPIProvider(Provider):
    name: ClassVar[str] = "scraperapi"
    priority: ClassVar[int] = 300  # last resort — costs credits
    free: ClassVar[bool] = True  # free tier exists

    def _key(self) -> str:
        # Read at call time so test env-patching is respected
        return os.getenv("SCRAPERAPI_KEY", "")

    def available(self) -> bool:
        return bool(self._key())

    def can_handle(self, url: str) -> bool:
        return self.available()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        key = self._key()
        if not key:
            raise ProviderError("SCRAPERAPI_KEY not set")
        params = {
            "api_key": key,
            "url": url,
            "render": "true" if opts.get("render") else "false",
        }
        if opts.get("country"):
            params["country_code"] = opts["country"]
        endpoint = f"https://api.scraperapi.com/?{urlencode(params)}"
        try:
            with httpx.Client(timeout=float(opts.get("timeout", 60))) as client:
                r = client.get(endpoint)
        except httpx.HTTPError as e:
            raise ProviderError(f"scraperapi: {e}") from e
        if r.status_code == 429:
            raise ProviderError("scraperapi: rate limited (429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"scraperapi: auth failed ({r.status_code})")
        return FetchResult(
            url=url,
            final_url=url,
            body=r.content,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
        )


register(ScraperAPIProvider())
