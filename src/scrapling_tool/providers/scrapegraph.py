"""ScrapeGraphAI provider — LLM-powered smart scraping and extraction.

Auth: set ``SCRAPEGRAPH_API_KEY`` or ``SCRAPGRAPH_API_KEY`` in env (defaults to configured key).
Supports smart extraction, markdown conversion, and AI web scraping.
"""
from __future__ import annotations

import json
import os
from typing import Any, ClassVar

import httpx

from scrapling_tool import credentials
from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register



class ScrapeGraphProvider(Provider):
    name: ClassVar[str] = "scrapegraph"
    priority: ClassVar[int] = 180
    free: ClassVar[bool] = True

    def _key(self) -> str:
        return credentials.get_key("scrapegraph")

    def available(self) -> bool:
        return bool(self._key())

    def can_handle(self, url: str) -> bool:
        return self.available()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        key = self._key()
        if not key:
            raise ProviderError("SCRAPEGRAPH_API_KEY not set")

        prompt = opts.get("prompt", "Extract main article text, titles, metadata, and structured links.")
        endpoint = "https://api.scrapegraphai.com/v1/smartscraper"
        payload = {
            "user_prompt": prompt,
            "website_url": url,
        }
        headers = {
            "Sgai-ApiKey": key,
            "Authorization": f"Bearer {key}",
            "x-api-key": key,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=float(opts.get("timeout", 60))) as client:
                r = client.post(endpoint, headers=headers, json=payload)
                if r.status_code == 404:
                    # Fallback to markdown endpoint if smartscraper is unavailable
                    alt_endpoint = "https://api.scrapegraphai.com/v1/markdown"
                    r = client.post(alt_endpoint, headers=headers, json={"website_url": url})
        except httpx.HTTPError as e:
            raise ProviderError(f"scrapegraph: {e}") from e

        if r.status_code == 429:
            raise ProviderError("scrapegraph: rate limited (429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"scrapegraph: auth failed ({r.status_code})")
        if r.status_code != 200:
            raise ProviderError(f"scrapegraph: http error {r.status_code}")

        data = r.json()
        result_content = data.get("result", data.get("result_output", data))
        if isinstance(result_content, (dict, list)):
            body_bytes = json.dumps(result_content, indent=2).encode("utf-8")
        else:
            body_bytes = str(result_content).encode("utf-8")

        return FetchResult(
            url=url,
            final_url=url,
            body=body_bytes,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
            meta={"request_id": data.get("request_id", ""), "raw": data},
        )


register(ScrapeGraphProvider())
