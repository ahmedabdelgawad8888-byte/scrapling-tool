"""TikTok and YouTube oEmbed providers — free, no auth.

The official oEmbed endpoints are documented, unauthenticated, and
capped only by sensible per-IP rate limits. They return rich metadata
(thumbnail, author, title, html embed) without any of the JS / captcha
baggage of the public website.

TikTok oEmbed: https://www.tiktok.com/oembed?url=<video_url>
YouTube oEmbed: https://www.youtube.com/oembed?url=<video_url>&format=json
"""
from __future__ import annotations

from typing import Any, ClassVar

import httpx

from scrapling_tool.providers.base import FetchResult, Provider, ProviderError, register


class _OembedBase(Provider):
    _endpoint: ClassVar[str] = ""

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        if not self._endpoint:
            raise ProviderError("oembed endpoint not configured")
        try:
            with httpx.Client(
                timeout=float(opts.get("timeout", 15)),
                follow_redirects=True,
                headers={"User-Agent": "scrapling-tool/1.1 (+oembed)"},
            ) as client:
                r = client.get(self._endpoint, params={"url": url})
        except httpx.HTTPError as e:
            raise ProviderError(f"oembed: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"oembed: HTTP {r.status_code} for {url}")
        return FetchResult(
            url=url,
            final_url=url,
            body=r.content,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
            meta={"oembed": r.json()},
        )


class TikTokOembedProvider(_OembedBase):
    name: ClassVar[str] = "tiktok_oembed"
    priority: ClassVar[int] = 10
    free: ClassVar[bool] = True
    _endpoint: ClassVar[str] = "https://www.tiktok.com/oembed"


class YouTubeOembedProvider(_OembedBase):
    name: ClassVar[str] = "youtube_oembed"
    priority: ClassVar[int] = 10
    free: ClassVar[bool] = True
    _endpoint: ClassVar[str] = "https://www.youtube.com/oembed"


register(TikTokOembedProvider())
register(YouTubeOembedProvider())
