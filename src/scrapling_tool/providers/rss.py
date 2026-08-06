"""RSS / Atom feed provider — free, no auth.

Many sites (news, blogs, podcasts, YouTube channels, Substack, Medium)
publish an RSS or Atom feed at ``/feed``, ``/rss``, ``/atom.xml`` or
discoverable via ``<link rel="alternate" type="application/rss+xml">``.
"""
from __future__ import annotations

from typing import Any, ClassVar
from xml.etree import ElementTree as ET

import httpx

from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register


class RssProvider(Provider):
    name: ClassVar[str] = "rss"
    priority: ClassVar[int] = 25
    free: ClassVar[bool] = True

    _FEED_HINTS = (".xml", "/feed", "/rss", "/atom", "atom.xml")

    def can_handle(self, url: str) -> bool:
        return any(h in url.lower() for h in self._FEED_HINTS)

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        try:
            with httpx.Client(
                timeout=float(opts.get("timeout", 30)),
                follow_redirects=True,
                headers={
                    "User-Agent": "scrapling-tool/1.1 (+rss)",
                    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5",
                },
            ) as client:
                r = client.get(url)
        except httpx.HTTPError as e:
            raise ProviderError(f"rss: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"rss: HTTP {r.status_code}")
        items = self._parse_items(r.text)
        return FetchResult(
            url=url,
            final_url=str(r.url),
            body=r.content,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
            meta={"items": items, "count": len(items)},
        )

    @staticmethod
    def _parse_items(text: str) -> list[dict[str, str]]:
        """Return ``[{title, link, published, summary}]`` for RSS or Atom."""
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        items: list[dict[str, str]] = []
        for entry in root.iter():
            tag = entry.tag.split("}", 1)[-1]
            if tag == "item":  # RSS
                items.append(
                    {
                        "title": (entry.findtext("title") or "").strip(),
                        "link": (entry.findtext("link") or "").strip(),
                        "published": (entry.findtext("pubDate") or "").strip(),
                        "summary": (entry.findtext("description") or "").strip(),
                    }
                )
            elif tag == "entry":  # Atom
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_el.get("href", "") if link_el is not None else ""
                items.append(
                    {
                        "title": (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip(),
                        "link": link,
                        "published": (
                            entry.findtext("{http://www.w3.org/2005/Atom}published")
                            or entry.findtext("{http://www.w3.org/2005/Atom}updated")
                            or ""
                        ).strip(),
                        "summary": (
                            entry.findtext("{http://www.w3.org/2005/Atom}summary")
                            or entry.findtext("{http://www.w3.org/2005/Atom}content")
                            or ""
                        ).strip(),
                    }
                )
        return items


register(RssProvider())
