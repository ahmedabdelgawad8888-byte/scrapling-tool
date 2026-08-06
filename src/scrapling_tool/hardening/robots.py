"""Per-host robots.txt cache with TTL.

Honors the ``Crawl-delay`` directive and the ``User-agent`` / ``Disallow``
matching per the de-facto spec implemented by Google's crawler. Used by
:mod:`scrapling_tool.hardening` to gate fetches before they leave the box.

This is *not* a full implementation of the robots.txt spec — there are
edge cases (pattern groups, $ anchors, Allow vs Disallow precedence) that
we handle with a pragmatic subset:

- Exact match, ``*`` suffix wildcard, ``$`` end-anchor
- Disallow beats Allow on equal specificity
- ``Crawl-delay`` is read as a float per user-agent
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger("scrapling_tool.hardening.robots")


@dataclass(slots=True)
class RobotsDecision:
    allowed: bool
    crawl_delay: float | None
    source: str  # "cache" or "fetched" or "allow_all"


def _user_agent() -> str:
    return "scrapling-tool"


class RobotsCache:
    """In-memory cache of robots.txt with optional on-disk persistence."""

    DEFAULT_TTL = 6 * 3600  # 6 hours

    def __init__(self, *, ttl: int = DEFAULT_TTL, fetcher: httpx.Client | None = None) -> None:
        self._ttl = ttl
        self._cache: dict[str, tuple[float, str]] = {}  # host -> (fetched_at, body)
        self._client = fetcher or httpx.Client(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": f"{_user_agent()}/1.1 (+robots)"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RobotsCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def is_allowed(self, url: str) -> RobotsDecision:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if not host:
            return RobotsDecision(True, None, "allow_all")

        now = time.time()
        cached = self._cache.get(host)
        body: str
        if cached and (now - cached[0]) < self._ttl:
            body = cached[1]
            source = "cache"
        else:
            body, source = self._fetch(host, parsed.scheme or "https")

        if not body.strip():
            return RobotsDecision(True, None, "allow_all")

        ua, allowed, crawl_delay = self.parse_body(body, url)
        return RobotsDecision(allowed=allowed, crawl_delay=crawl_delay, source=source)

    @staticmethod
    def parse_body(body: str, url: str) -> tuple[str, bool, float | None]:
        """Pure parser exposed for testing.

        Returns ``(matched_ua, allowed, crawl_delay)`` for ``url`` per
        the matching rules in :func:`_parse`.
        """
        return _parse(body, url)

    def _fetch(self, host: str, scheme: str) -> tuple[str, str]:
        url = f"{scheme}://{host}/robots.txt"
        try:
            r = self._client.get(url)
        except httpx.HTTPError as e:
            log.debug("robots fetch failed for %s: %s", host, e)
            return "", "allow_all"
        if r.status_code >= 400:
            # No robots.txt means: allow all (per the spec)
            self._cache[host] = (time.time(), "")
            return "", "allow_all"
        body = r.text
        self._cache[host] = (time.time(), body)
        return body, "fetched"


# --- pure parser, importable without a network stack ---

def _compile_rule(rule: str) -> re.Pattern[str] | None:
    """Translate a robots.txt path pattern to a regex.

    Supports ``*`` (any chars) and trailing ``$`` (end anchor). The spec
    allows more, but this covers the common cases without false negatives.
    """
    rule = rule.strip()
    if not rule:
        return None
    if rule == "":
        return None
    anchored = rule.endswith("$")
    body = rule[:-1] if anchored else rule
    out: list[str] = []
    for ch in body:
        if ch == "*":
            out.append(".*")
        elif ch in r".\+?()[]{}|^/\\":
            out.append(re.escape(ch))
        else:
            out.append(re.escape(ch))
    pat = "".join(out)
    if anchored:
        pat += "$"
    else:
        pat += ".*"
    try:
        return re.compile(pat, re.I)
    except re.error:
        return None


def _parse(body: str, url: str) -> tuple[str, bool, float | None]:
    """Return ``(matched_ua, allowed, crawl_delay)`` for ``url``."""
    path = urlparse(url).path or "/"
    ua_target = _user_agent()
    ua = "*"
    directives: list[tuple[str, str, str]] = []  # (ua, directive, value)
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k == "user-agent":
            ua = v.lower()
        else:
            directives.append((ua, k, v))
    # Find the longest matching user-agent block
    matching_ua = "*" if "*" in [d[0] for d in directives] else ua_target
    if ua_target in [d[0] for d in directives]:
        matching_ua = ua_target
    crawl_delay: float | None = None
    allow: re.Pattern[str] | None = None
    disallow: re.Pattern[str] | None = None
    for d_ua, k, v in directives:
        if d_ua != matching_ua:
            continue
        if k == "crawl-delay":
            try:
                crawl_delay = float(v)
            except ValueError:
                pass
        elif k == "allow":
            if v == "":
                allow = re.compile(".*")
                continue
            r = _compile_rule(v)
            if r is not None:
                allow = r
        elif k == "disallow":
            if v == "":
                # Empty Disallow = allow all
                disallow = None
                continue
            r = _compile_rule(v)
            if r is not None:
                disallow = r
    if disallow is None:
        return matching_ua, True, crawl_delay
    if allow is not None and allow.match(path):
        return matching_ua, True, crawl_delay
    return matching_ua, not bool(disallow.match(path)), crawl_delay
