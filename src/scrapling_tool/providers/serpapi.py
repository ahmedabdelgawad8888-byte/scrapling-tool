"""SerpApi provider — Google Search, Maps, and News API integration.

Auth: set ``SERPAPI_API_KEY`` in env (defaults to configured key).
Supports Authorization Bearer header and query param authentication.
"""
from __future__ import annotations

import json
import os
from typing import Any, ClassVar

import httpx

from scrapling_tool import credentials
from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register



class SerpApiProvider(Provider):
    name: ClassVar[str] = "serpapi"
    priority: ClassVar[int] = 170
    free: ClassVar[bool] = True

    def _key(self) -> str:
        return credentials.get_key("serpapi")

    def available(self) -> bool:
        return bool(self._key())

    def can_handle(self, url: str) -> bool:
        return self.available()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        key = self._key()
        if not key:
            raise ProviderError("SERPAPI_API_KEY not set")

        endpoint = "https://serpapi.com/search.json"
        params = {
            "q": url,
            "api_key": key,
            "engine": opts.get("engine", "google"),
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=float(opts.get("timeout", 30))) as client:
                r = client.get(endpoint, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"serpapi: {e}") from e

        if r.status_code == 429:
            raise ProviderError("serpapi: rate limited (429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"serpapi: auth failed ({r.status_code})")
        if r.status_code != 200:
            raise ProviderError(f"serpapi: http error {r.status_code}")

        data = r.json()
        body_json = json.dumps(data, indent=2).encode("utf-8")

        return FetchResult(
            url=url,
            final_url=url,
            body=body_json,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
            meta={"organic_results": data.get("organic_results", [])},
        )


register(SerpApiProvider())
