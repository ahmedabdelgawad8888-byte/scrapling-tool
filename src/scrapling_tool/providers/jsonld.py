"""JSON-LD extraction — free, no auth.

JSON-LD (``<script type="application/ld+json">``) is what publishers use
to tell Google about their content. It is by far the most stable and
parseable source of structured data on the open web. Many sites have
*only* JSON-LD and no RSS / sitemap.
"""
from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from scrapling_tool.providers.base import FetchResult, Provider, register

# A page that has *any* JSON-LD block
_HAS_LD = re.compile(
    rb'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


class JsonLdProvider(Provider):
    """Pass-through provider: extract JSON-LD from a fetched HTML.

    This provider delegates the actual HTTP fetch to the ``direct`` provider
    (httpx) and decorates the result with a ``meta["jsonld"]`` field. It is
    registered at higher priority than the generic scrapling provider so
    that sites with rich structured data short-circuit browser rendering.
    """

    name: ClassVar[str] = "jsonld"
    priority: ClassVar[int] = 30
    free: ClassVar[bool] = True

    def __init__(self) -> None:
        # Lazy lookup to avoid circular import at module load
        from scrapling_tool.providers.direct import DirectProvider

        self._direct: DirectProvider = DirectProvider()

    def fetch(self, url: str, **opts: Any) -> FetchResult:
        result = self._direct.fetch(url, **opts)
        jsonld: list[dict[str, Any]] = []
        if result.body:
            for m in _HAS_LD.finditer(result.body):
                raw = m.group(1).decode("utf-8", "replace").strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except (ValueError, TypeError):
                    # JSON-LD can be a top-level array, fix that
                    try:
                        parsed = json.loads(f"[{raw}]")
                    except ValueError:
                        continue
                if isinstance(parsed, list):
                    jsonld.extend(x for x in parsed if isinstance(x, dict))
                elif isinstance(parsed, dict):
                    jsonld.append(parsed)
        result.meta["jsonld"] = jsonld
        result.meta["jsonld_count"] = len(jsonld)
        result.provider = self.name
        return result


register(JsonLdProvider())
