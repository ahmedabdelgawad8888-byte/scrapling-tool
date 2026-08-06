"""Scrapling-backed provider.

The whole point of the project: use scrapling's three fetchers at the right
elevation.

- ``mode="http"``     — :class:`scrapling.Fetcher` (no JS, TLS-impersonating)
- ``mode="stealth"``  — :class:`scrapling.StealthyFetcher` (real browser,
                        cloudflare-friendly, slow)
- ``mode="browser"``  — :class:`scrapling.DynamicFetcher` (real browser via
                        Playwright, JS-rendered, slowest)
"""
from __future__ import annotations

from typing import Any, ClassVar

# scrapling is the *whole point* of this package — guaranteed present
from scrapling import DynamicFetcher, Fetcher, StealthyFetcher  # type: ignore  # noqa: E402

from scrapling_tool.providers.base import FetchResult, Provider, register


class ScraplingProvider(Provider):
    name: ClassVar[str] = "scrapling"
    priority: ClassVar[int] = 200  # fallback after the free-paths
    free: ClassVar[bool] = True

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        mode = (opts.get("mode") or "http").lower()
        timeout = int(opts.get("timeout") or 30)
        if mode == "stealth":
            page = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=timeout * 1000,
            )
        elif mode == "browser":
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=timeout * 1000,
            )
        else:
            page = Fetcher.get(url, timeout=timeout)

        # scrapling's Adaptor exposes response metadata
        body = page.body if isinstance(page.body, (bytes, bytearray)) else str(page.body).encode("utf-8")
        return FetchResult(
            url=url,
            final_url=url,
            body=bytes(body),
            status=200,
            headers={"content-type": "text/html; charset=utf-8"},
            provider=self.name,
            meta={"page": page, "mode": mode},
        )


register(ScraplingProvider())
