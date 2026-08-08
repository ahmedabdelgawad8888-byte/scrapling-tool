"""Serper provider — Google Search API integration.

Auth: set ``SERPER_API_KEY`` in env (defaults to configured key).
"""
from __future__ import annotations

import json
import os
from typing import Any, ClassVar

import httpx

from scrapling_tool import credentials
from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register



class SerperProvider(Provider):
    name: ClassVar[str] = "serper"
    priority: ClassVar[int] = 160
    free: ClassVar[bool] = True

    def _key(self) -> str:
        return credentials.get_key("serper")

    def available(self) -> bool:
        return bool(self._key())

    def can_handle(self, url: str) -> bool:
        return self.available()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        key = self._key()
        if not key:
            raise ProviderError("SERPER_API_KEY not set")

        endpoint = "https://google.serper.dev/search"
        payload = {"q": f"site:{url}" if not url.startswith("http") else url, "num": 10}
        headers = {"X-API-KEY": key, "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=float(opts.get("timeout", 30))) as client:
                r = client.post(endpoint, headers=headers, json=payload)
        except httpx.HTTPError as e:
            raise ProviderError(f"serper: {e}") from e

        if r.status_code == 429:
            raise ProviderError("serper: rate limited (429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"serper: auth failed ({r.status_code})")
        if r.status_code != 200:
            raise ProviderError(f"serper: http error {r.status_code}")

        data = r.json()
        body_json = json.dumps(data, indent=2).encode("utf-8")

        return FetchResult(
            url=url,
            final_url=url,
            body=body_json,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
            meta={"organic": data.get("organic", [])},
        )


register(SerperProvider())
