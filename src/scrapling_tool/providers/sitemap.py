"""Sitemap discovery — free, no auth.

Most sites publish ``/sitemap.xml`` (or a ``sitemap_index.xml`` that points
at per-section sitemaps). Fetching it is *much* cheaper than crawling, and
it identifies every page the site *wants* indexed. This provider is the
right starting point for any site you don't already know.
"""
from __future__ import annotations

import re
from typing import Any, ClassVar
from xml.etree import ElementTree as ET

import httpx

from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register

_SITEMAP_LOC = re.compile(rb"<loc>([^<]+)</loc>", re.I)
_SITEMAP_INDEX_TAG = re.compile(rb"<sitemapindex", re.I)


class SitemapProvider(Provider):
    name: ClassVar[str] = "sitemap"
    priority: ClassVar[int] = 20  # cheap discovery — try first
    free: ClassVar[bool] = True

    def can_handle(self, url: str) -> bool:
        return url.lower().endswith(".xml") or "sitemap" in url.lower()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        try:
            with httpx.Client(
                timeout=float(opts.get("timeout", 30)),
                follow_redirects=True,
                headers={"User-Agent": "scrapling-tool/1.1 (+sitemap)"},
            ) as client:
                r = client.get(url)
        except httpx.HTTPError as e:
            raise ProviderError(f"sitemap: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"sitemap: HTTP {r.status_code}")
        body = r.content
        urls = [m.group(1).decode("utf-8", "replace") for m in _SITEMAP_LOC.finditer(body)]
        is_index = bool(_SITEMAP_INDEX_TAG.search(body))
        meta = {"urls": urls, "is_index": is_index, "count": len(urls)}
        return FetchResult(
            url=url,
            final_url=str(r.url),
            body=body,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
            meta=meta,
        )

    @staticmethod
    def parse_xml(text: str) -> list[str]:
        """Strict XML fallback for callers that want parsed entries.

        Used by the CLI's ``--from-sitemap`` flag. Returns plain <loc> entries
        for both regular sitemaps and sitemap indexes.
        """
        out: list[str] = []
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return out
        for loc in root.iter():
            if loc.tag.endswith("loc") and loc.text:
                out.append(loc.text.strip())
        return out


register(SitemapProvider())
