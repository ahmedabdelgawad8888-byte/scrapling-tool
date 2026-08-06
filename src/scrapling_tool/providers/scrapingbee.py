"""Scrapingbee provider — 1,000 free trial credits.

Auth: set ``SCRAPINGBEE_API_KEY`` in env. The trial quota is large enough
for development and small CI runs; the hardened layer surfaces 429 as
:class:`ProviderError` and the CLI lets you swap providers on the fly.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar

import httpx

from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register


class ScrapingbeeProvider(Provider):
    name: ClassVar[str] = "scrapingbee"
    priority: ClassVar[int] = 305
    free: ClassVar[bool] = True  # trial is free

    def _key(self) -> str:
        return os.getenv("SCRAPINGBEE_API_KEY", "")

    def available(self) -> bool:
        return bool(self._key())

    def can_handle(self, url: str) -> bool:
        return self.available()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        key = self._key()
        if not key:
            raise ProviderError("SCRAPINGBEE_API_KEY not set")
        params: dict[str, Any] = {"url": url, "api_key": key}
        if opts.get("render"):
            params["render_js"] = "true"
        if opts.get("country"):
            params["country_code"] = opts["country"]
        try:
            with httpx.Client(timeout=float(opts.get("timeout", 60))) as client:
                r = client.get("https://app.scrapingbee.com/api/v1/", params=params)
        except httpx.HTTPError as e:
            raise ProviderError(f"scrapingbee: {e}") from e
        if r.status_code == 429:
            raise ProviderError("scrapingbee: trial quota exhausted (429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"scrapingbee: auth failed ({r.status_code})")
        return FetchResult(
            url=url,
            final_url=url,
            body=r.content,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
        )


register(ScrapingbeeProvider())
