"""Direct HTTP provider via httpx.

Cheapest path of all: no proxy, no credential, no captcha-solver. Used
when the target allows anonymous access.
"""
from __future__ import annotations

from typing import Any, ClassVar

import httpx

from scrapling_tool.providers.base import FetchResult, Provider, register


class DirectProvider(Provider):
    name: ClassVar[str] = "direct"
    priority: ClassVar[int] = 50  # cheap and stable — try early
    free: ClassVar[bool] = True

    def __init__(self, *, timeout: float = 30.0, user_agent: str | None = None) -> None:
        self._timeout = timeout
        self._ua = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        headers = dict(opts.get("headers") or {})
        headers.setdefault("User-Agent", self._ua)
        headers.setdefault("Accept", "*/*")
        if opts.get("if_none_match"):
            headers["If-None-Match"] = opts["if_none_match"]
        if opts.get("if_modified_since"):
            headers["If-Modified-Since"] = opts["if_modified_since"]
        timeout = float(opts.get("timeout") or self._timeout)
        with httpx.Client(timeout=timeout, follow_redirects=True, http2=True) as client:
            r = client.get(url, headers=headers)
        return FetchResult(
            url=url,
            final_url=str(r.url),
            body=r.content,
            status=r.status_code,
            headers={k: v for k, v in r.headers.items()},
            provider=self.name,
        )


register(DirectProvider())
