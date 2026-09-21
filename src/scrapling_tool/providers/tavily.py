"""Tavily provider — AI-powered search & content extraction.

Auth: set ``TAVILY_API_KEY`` in env (defaults to configured key).
Supports both search queries and URL raw content extraction.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar

import httpx

from scrapling_tool import credentials
from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register



class TavilyProvider(Provider):
    name: ClassVar[str] = "tavily"
    priority: ClassVar[int] = 150
    free: ClassVar[bool] = True

    def _key(self) -> str:
        return credentials.get_key("tavily")

    def available(self) -> bool:
        return bool(self._key())

    def can_handle(self, url: str) -> bool:
        return self.available()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        key = self._key()
        if not key:
            raise ProviderError("TAVILY_API_KEY not set")

        endpoint = "https://api.tavily.com/extract"
        payload = {
            "api_key": key,
            "urls": [url],
        }
        try:
            with httpx.Client(timeout=float(opts.get("timeout", 30))) as client:
                r = client.post(endpoint, json=payload)
        except httpx.HTTPError as e:
            raise ProviderError(f"tavily: {e}") from e

        if r.status_code == 429:
            raise ProviderError("tavily: rate limited (429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"tavily: auth failed ({r.status_code})")
        if r.status_code != 200:
            raise ProviderError(f"tavily: http error {r.status_code}")

        data = r.json()
        results = data.get("results", [])
        extracted_text = ""
        if results and isinstance(results, list):
            extracted_text = results[0].get("raw_content", "") or results[0].get("content", "")

        return FetchResult(
            url=url,
            final_url=url,
            body=extracted_text.encode("utf-8"),
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
            meta={"results": results},
        )


register(TavilyProvider())
